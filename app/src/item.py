from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)
import math

from .attrib import Attrib
from .progress import Progress
from .db_serializable import (
    Identifiable, MutableNamespace, coldef, new_game_data)

tables_to_create = {
    'items': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        progress_id integer,
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """
}

class Source:
    def __init__(self, new_id=0):
        self.item = Item(new_id)
        self.preserve = False  # if true then source will not be consumed
        self.quantity = 1

    def to_json(self):
        return {
            'source_id': self.item.id,
            'preserve': self.preserve,
            'quantity': self.quantity}

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.item = Item(int(data.get('source_id', 0)))
        instance.preserve = data.get('preserve', False)
        instance.quantity = data.get('quantity', 1)
        return instance

class Recipe:
    def __init__(self, new_id=0):
        self.id = new_id  # needed in db, not useful in JSON files
        self.instant = False
        self.rate_amount = 1  # quantity produced per batch
        self.rate_duration = 1.0  # seconds for a batch
        self.sources = []  # Source objects
        self.attrib = None  # tuple (attrib_id, val)

    def to_json(self):
        return {
            'id': self.id,
            'instant': self.instant,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'sources': [
                source.to_json()
                for source in self.sources]}

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.id = data.get('id', 0)
        instance.instant = data.get('instant', False)
        instance.rate_amount = data.get('rate_amount', 1)
        instance.rate_duration = data.get('rate_duration', 1.0)
        instance.sources = [
            Source.from_json(src_data)
            for src_data in data.get('sources', [])]
        return instance

class Item(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.get_list()) > 1 else True
        self.attribs = {}  # Attrib objects and their stat val
        self.recipes = []  # list of Recipe objects
        self.progress = Progress(entity=self)

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'recipes': [
                recipe.to_json()
                for recipe in self.recipes],
            'attribs': {
                attrib.id: val
                for attrib, val in self.attribs.items()},
            'progress': self.progress.to_json(),
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.toplevel = data.get('toplevel', False)
        instance.attribs = {
            Attrib(int(attrib_id)): val
            for attrib_id, val in data.get('attribs', {}).items()}
        instance.progress = Progress.from_json(
            data.get('progress', {}), instance)
        instance.recipes = [
            Recipe.from_json(recipe_data)
            for recipe_data in data.get('recipes', [])]
        return instance

    def json_to_db(self, doc):
        print(f"{self.__class__.__name__}.json_to_db()")
        self.progress.json_to_db(doc['progress'])
        doc['progress_id'] = self.progress.id
        super().json_to_db(doc)
        for rel_table in ('item_attribs', 'item_sources'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE item_id = %s AND game_token = %s
            """, (self.id, self.game_token))
        if doc['attribs']:
            values = [
                (g.game_token, self.id, attrib_id, val)
                for attrib_id, val in doc['attribs'].items()]
            self.insert_multiple(
                "item_attribs",
                "game_token, item_id, attrib_id, value",
                values)
        if doc['recipes']:
            print(f"recipes: {doc['recipes']}")
            values = []
            for recipe_id, recipe_data in enumerate(doc['recipes'], start=1):
                for source_data in recipe_data['sources']:
                    values.append((
                        g.game_token,
                        self.id,
                        recipe_id,
                        source_data['source_id'],
                        source_data['quantity'],
                        source_data['preserve'],
                        recipe_data['rate_amount'],
                        recipe_data['rate_duration'],
                        recipe_data['instant']
                        ))
            self.insert_multiple(
                "item_sources",
                "game_token, item_id, recipe_id, source_id,"
                " quantity, preserve, rate_amount, rate_duration, instant",
                values)

    @classmethod
    def db_item_and_progress_data(cls, id_to_get=None):
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """
        values = [g.game_token]
        if id_to_get:
            query += f"AND {{tables[0]}}.id = %s\n"
            values.append(id_to_get);
        query += "ORDER BY {tables[0]}.name\n"
        return cls.select_tables(
            query, values, ['items', 'progress'])

    @classmethod
    def db_attrib_data(cls, id_to_get=None, include_all=False):
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.attrib_id = {tables[0]}.id
        """
        values = [g.game_token]
        if id_to_get:
            query += "AND {tables[1]}.item_id = %s\n"
            values = [id_to_get] + values
        query += "WHERE {tables[0]}.game_token = %s\n"
        if include_all:
            query += "ORDER BY {tables[0]}.name\n"
        else:
            query += "AND {tables[1]}.item_id IS NOT NULL\n"
        return cls.select_tables(
            query, values, ['attribs', 'item_attribs'])

    @classmethod
    def db_source_data(cls, id_to_get=None, get_by_source=False):
        query = """
            SELECT *
            FROM item_sources
            WHERE game_token = %s
        """
        values = [g.game_token]
        if id_to_get:
            id_field = 'source_id' if get_by_source else 'item_id'
            query = f"{query}\nAND {id_field} = %s"
            values.append(id_to_get);
        sources_data = cls.execute_select(query, values)
        item_recipes_data = {}
        for row in sources_data:
            recipes_data = item_recipes_data.setdefault(row.item_id, {})
            recipe_data = recipes_data.setdefault(row.recipe_id, row)
            recipe_data.setdefault('sources', []).append(row)
        return item_recipes_data

    @classmethod
    def from_db(cls, id_to_get):
        return cls._from_db(id_to_get)

    @classmethod
    def list_from_db(cls):
        return cls._from_db()

    @classmethod
    def _from_db(cls, id_to_get=None):
        print(f"{cls.__name__}._from_db()")
        # Get item and progress data
        tables_rows = cls.db_item_and_progress_data(id_to_get)
        instances = {}  # keyed by ID
        for item_data, progress_data in tables_rows:
            instance = instances.setdefault(
                item_data.id, cls.from_json(vars(item_data)))
            if progress_data.id:
                instance.progress = Progress.from_json(progress_data, instance)
        # Get attrib data for items
        tables_rows = cls.db_attrib_data(id_to_get)
        for attrib_data, item_attrib_data in tables_rows:
            instance = instances[item_attrib_data.item_id]
            attrib_obj = Attrib(attrib_data.id)
            instance.attribs[attrib_obj] = item_attrib_data.value
        # Get source data for items
        item_recipes_data = cls.db_source_data(id_to_get)
        for item_id, recipes_data in item_recipes_data.items():
            instance = instances[item_id]
            instance.recipes = [
                Recipe.from_json(recipe_data)
                for recipe_data in recipes_data.values()]
        # Print debugging info
        print(f"found {len(instances)} items")
        for instance in instances.values():
            print(f"item {instance.id} ({instance.name})"
                f" has {len(instance.recipes)} recipes")
            if len(instance.recipes):
                recipe = instance.recipes[0]
                print(f"rate_amount={recipe.rate_amount}")
                print(f"instant={recipe.instant}")
                for source in recipe.sources:
                    print(f"item id {source.item.id} qty {source.quantity}")
        # Convert and return
        instances = list(instances.values())
        if id_to_get is not None and len(instances) == 1:
            return instances[0]
        return instances

    @classmethod
    def data_for_configure(cls, config_id):
        print(f"{cls.__name__}.data_for_configure()")
        if config_id == 'new':
            config_id = 0
        else:
            config_id = int(config_id)
        # Get all item data and the current item's progress data
        tables_rows = cls.db_item_and_progress_data()
        g.game_data.items = []
        current_data = MutableNamespace()
        for item_data, progress_data in tables_rows:
            if item_data.id == config_id:
                current_data = item_data
                if progress_data.id:
                    item_data.progress = progress_data
            g.game_data.items.append(Item.from_json(item_data))
        # Get all attrib data and the current item's attrib relation data
        tables_rows = cls.db_attrib_data(config_id, include_all=True)
        for attrib_data, item_attrib_data in tables_rows:
            if item_attrib_data.attrib_id:
                current_data.setdefault(
                    'attribs', {})[attrib_data.id] = item_attrib_data.value
            g.game_data.attribs.append(Attrib.from_json(attrib_data))
        # Get the current item's source relation data
        item_recipes_data = cls.db_source_data(config_id)
        if item_recipes_data:
            recipes_data = list(item_recipes_data.values())[0]
            current_data.recipes = list(recipes_data.values())
        # Create item from data
        current_obj = Item.from_json(current_data)
        # Replace partial objects with fully populated objects
        populated_objs = {}
        for partial_attrib, val in current_obj.attribs.items():
            attrib = Attrib.get_by_id(partial_attrib.id)
            populated_objs[attrib] = val
        current_obj.attribs = populated_objs
        for recipe in current_obj.recipes:
            for source in recipe.sources:
                source.item = Item.get_by_id(source.item.id)
        # Print debugging info
        print(f"found {len(current_obj.recipes)} recipes")
        if len(current_obj.recipes):
            recipe = current_obj.recipes[0]
            print(f"rate_amount={recipe.rate_amount}")
            print(f"instant={recipe.instant}")
            for source in recipe.sources:
                print(f"item id {source.item.id} name {source.item.name}"
                    f" qty {source.quantity}")
        return current_obj

    @classmethod
    def data_for_play(cls, config_id):
        print(f"{cls.__name__}.data_for_play()")
        current_obj = cls.data_for_configure(config_id)
        # Get relation data for items that use this item as a source
        item_recipes_data = cls.db_source_data(config_id, get_by_source=True)
        for item_id, recipes_data in item_recipes_data.items():
            print(f"item_id {item_id}, recipes_data {recipes_data}")
            item = Item.get_by_id(item_id)
            item.recipes = [
                Recipe.from_json(recipe_data)
                for recipe_id, recipe_data in recipes_data.items()]
        return current_obj

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            self.name = request.form.get('item_name')
            self.description = request.form.get('item_description')
            self.toplevel = bool(request.form.get('top_level'))
            if self.progress.is_ongoing:
                self.progress.stop()
            else:
                self.progress.quantity = self.form_int(request, 'item_quantity')
            self.progress = Progress.from_json({
                'id': self.progress.id,
                'quantity': self.progress.quantity,
                'q_limit': self.form_int(request, 'item_limit')})
            recipe_ids = request.form.getlist('recipe_id')
            self.recipes = []
            for recipe_id in recipe_ids:
                recipe = Recipe()
                self.recipes.append(recipe)
                recipe.rate_amount = int(request.form.get(
                    f'recipe_{recipe_id}_rate_amount'))
                recipe.rate_duration = int(request.form.get(
                    f'recipe_{recipe_id}_rate_duration'))
                recipe.instant = bool(request.form.get(
                    f'recipe_{recipe_id}_instant'))
                source_ids = request.form.getlist(
                    f'recipe_{recipe_id}_source_id')
                print(f"Source IDs: {source_ids}")
                for source_id in source_ids:
                    source = Source.from_json({
                        'source_id': int(source_id),
                        'quantity': int(request.form.get(
                            f'recipe_{recipe_id}_source_{source_id}_quantity', 0)),
                        'preserve': bool(request.form.get(
                            f'recipe_{recipe_id}_source_{source_id}_preserve')),
                    })
                    recipe.sources.append(source)
                    print(f"Sources for {recipe_id}: ",
                        {source.item.id: source.quantity
                        for source in recipe.sources})
            attrib_ids = request.form.getlist('attrib_id')
            print(f"Attrib IDs: {attrib_ids}")
            self.attribs = {}
            for attrib_id in attrib_ids:
                attrib_val = int(
                    request.form.get(f'attrib_{attrib_id}_val', 0))
                attrib_obj = Attrib(attrib_id)
                self.attribs[attrib_obj] = attrib_val
            print("attribs: ", {attrib.id: val
                for attrib, val in self.attribs.items()})
            self.to_db()
        elif 'delete_item' in request.form:
            self.remove_from_db()
        elif 'cancel_changes' in request.form:
            print("Cancelling changes.")
        else:
            print("Neither button was clicked.")
        referrer = session.pop('referrer', None)
        print(f"Referrer in configure_by_form(): {referrer}")
        if referrer:
            return redirect(referrer)
        else:
            return redirect(url_for('configure'))

def set_routes(app):
    @app.route('/configure/item/<item_id>', methods=['GET', 'POST'])
    def configure_item(item_id):
        new_game_data()
        instance = Item.data_for_configure(item_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/item.html',
                current=instance,
                game_data=g.game_data)
        else:
            return instance.configure_by_form()

    @app.route('/play/item/<int:item_id>')
    def play_item(item_id):
        new_game_data()
        instance = Item.data_for_play(item_id)
        if not instance:
            return 'Item not found'
        return render_template(
            'play/item.html',
            current=instance,
            game_data=g.game_data)

    @app.route('/item/gain/<int:item_id>', methods=['POST'])
    def gain_item(item_id):
        print(f"gain_item({item_id})")
        quantity = int(request.form.get('quantity'))
        item = Item.get_by_id(item_id)
        print(f"Retrieved item {item.id} from DB: {len(item.recipes)} recipes")
        num_batches = math.floor(quantity / item.progress.step_size)
        changed = item.progress.change_quantity(num_batches)
        if changed:
            return jsonify({
                'status': 'success', 'message':
                f'Quantity of {item.name} changed.'})
        else:
            return jsonify({
                'status': 'error',
                'message': 'Could not change quantity.'})

    @app.route('/item/progress_data/<int:item_id>')
    def item_progress_data(item_id):
        print(f"item_progress_data({item_id})")
        item = Item.from_db(item_id)
        print(f"Retrieved item {item.id} from DB: {len(item.recipes)} recipes")
        if item:
            if item.progress.is_ongoing:
                item.progress.determine_current_quantity()
            return jsonify({
                'is_ongoing': item.progress.is_ongoing,
                'recipe_id': item.progress.recipe.id,
                'quantity': item.progress.quantity,
                'elapsed_time': item.progress.calculate_elapsed_time()})
        else:
            return jsonify({'error': 'Item not found'})

    @app.route('/item/start/<int:item_id>/<int:recipe_id>')
    def start_item(item_id, recipe_id):
        print(f"start_item({item_id}, {recipe_id})")
        item = Item.from_db(item_id)
        print(f"Retrieved item {item.id} from DB: {len(item.recipes)} recipes")
        if item.progress.start(recipe_id):
            item.to_db()
            return jsonify({
                'status': 'success',
                'message': 'Progress started.',
                'is_ongoing': item.progress.is_ongoing})
        else:
            return jsonify({
                'status': 'error',
                'message': 'Could not start.',
                'is_ongoing': item.progress.is_ongoing})

    @app.route('/item/stop/<int:item_id>')
    def stop_item(item_id):
        print(f"stop_item({item_id})")
        item = Item.from_db(item_id)
        print(f"Retrieved item {item.id} from DB: {len(item.recipes)} recipes")
        if item.progress.stop():
            item.to_db()
            return jsonify({
                'status': 'success',
                'message': 'Progress paused.',
                'is_ongoing': item.progress.is_ongoing})
        else:
            return jsonify({
                'status': 'success',
                'message': 'Progress is already paused.',
                'is_ongoing': item.progress.is_ongoing})

