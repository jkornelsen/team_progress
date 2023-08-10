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

class Recipe:
    def __init__(self, new_id=0):
        self.id = new_id  # needed in db, not useful in JSON files
        self.instant = False
        self.rate_amount = 1  # quantity produced per batch
        self.rate_duration = 1.0  # seconds for a batch
        self.sources = {}  # Items and their quantity

    def to_json(self):
        return {
            'id': self.id,
            'instant': self.instant,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'sources': {
                item.id: quantity
                for item, quantity in self.sources.items()}}

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.id = data.get('id', 0)
        instance.instant = data.get('instant', False)
        instance.rate_amount = data.get('rate_amount', 1)
        instance.rate_duration = data.get('rate_duration', 1.0)
        instance.sources = {
            Item(int(source_id)): quantity
            for source_id, quantity in data.get('sources', {}).items()}
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
            for recipe_id, recipe in enumerate(doc['recipes'], start=1):
                for source_id, src_qty in recipe['sources'].items():
                    values.append((
                        g.game_token, self.id, recipe_id, source_id,
                        src_qty,
                        recipe['rate_amount'],
                        recipe['rate_duration'],
                        recipe['instant']
                        ))
            self.insert_multiple(
                "item_sources",
                "game_token, item_id, recipe_id, source_id,"
                " src_qty, rate_amount, rate_duration, instant",
                values)

    @classmethod
    def from_db(cls, id_to_get):
        return cls._from_db(id_to_get)

    @classmethod
    def list_from_db(cls):
        return cls._from_db()

    @classmethod
    def _from_db(cls, id_to_get=None):
        print(f"{cls.__name__}._from_db()")
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            LEFT JOIN {tables[2]}
                ON {tables[2]}.item_id = {tables[0]}.id
                AND {tables[2]}.game_token = {tables[0]}.game_token
            LEFT JOIN {tables[3]}
                ON {tables[3]}.item_id = {tables[0]}.id
                AND {tables[3]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """
        values = [g.game_token]
        if id_to_get:
            query = f"{query}\nAND {{tables[0]}}.id = %s"
            values.append(id_to_get);
        tables_rows = cls.select_tables(
            query, values,
            ['items', 'progress', 'item_attribs', 'item_sources'])
        instances = {}  # keyed by ID
        for item_data, progress_data, attrib_data, source_data in tables_rows:
            print(f"item_data {item_data}")
            print(f"progress_data {progress_data}")
            print(f"attrib_data {attrib_data}")
            print(f"source_data {source_data}")
            instance = instances.get(item_data.id)
            if not instance:
                instance = cls.from_json(vars(item_data))
                instances[item_data.id] = instance
            if progress_data.id:
                instance.progress = Progress.from_json(progress_data, instance)
            if attrib_data.attrib_id:
                instance.attribs[attrib_data.attrib_id] = attrib_data.value
            if source_data.recipe_id:
                recipe = next(
                    (recipe for recipe in instance.recipes
                    if recipe.id == source_data.recipe_id), None)
                if not recipe:
                    recipe = Recipe.from_json(source_data)
                    instance.recipes.append(recipe)
                recipe.sources[source_data.source_id] = source_data.src_qty
        # Replace IDs with partial objects
        for instance in instances.values():
            attrib_objs = {}
            for attrib_id, attrib_val in instance.attribs.items():
                attrib_obj = Attrib(attrib_id)
                attrib_objs[attrib_obj] = attrib_val
            instance.attribs = attrib_objs
        for instance in instances.values():
            for recipe in instance.recipes:
                source_objs = {}
                for source_id, source_qty in recipe.sources.items():
                    source_obj = Item(source_id)
                    source_objs[source_obj] = source_qty
                recipe.sources = source_objs
        # Print debugging info
        print(f"found {len(instances)} items")
        for instance in instances.values():
            print(f"item {instance.id} ({instance.name})"
                " has {len(instance.recipes)} recipes")
            if len(instance.recipes):
                recipe = instance.recipes[0]
                print(f"rate_amount={recipe.rate_amount}")
                print(f"instant={recipe.instant}")
                for source_item, source_qty in recipe.sources.items():
                    print(f"item id {source_item.id} name {source_item.name}"
                        " qty {source_qty}")
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
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[0]}.id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (config_id, g.game_token), ['items', 'progress'])
        g.game_data.items = []
        current_data = MutableNamespace()
        for item_data, progress_data in tables_rows:
            if item_data.id == config_id:
                current_data = item_data
                if progress_data.id:
                    item_data.progress = progress_data
            g.game_data.items.append(Item.from_json(item_data))
        # Get all attrib data and the current item's attrib relation data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.attrib_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.item_id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (config_id, g.game_token), ['attribs', 'item_attribs'])
        for attrib_data, item_attrib_data in tables_rows:
            if item_attrib_data.attrib_id:
                current_data.setdefault(
                    'attribs', {})[attrib_data.id] = item_attrib_data.value
            g.game_data.attribs.append(Attrib.from_json(attrib_data))
        # Get the current item's source relation data
        sources_data = cls.execute_select("""
            SELECT *
            FROM item_sources
            WHERE game_token = %s
                AND item_id = %s
        """, (g.game_token, config_id))
        recipes_data = {}
        for row in sources_data:
            if row.recipe_id in recipes_data:
                recipe_data = recipes_data[row.recipe_id]
            else:
                recipe_data = row
                recipes_data[row.recipe_id] = recipe_data
            recipe_data.setdefault(
                'sources', {})[row.source_id] = row.src_qty
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
            populated_objs = {}
            for partial_item, qty in recipe.sources.items():
                item = Item.get_by_id(partial_item.id)
                populated_objs[item] = qty
            recipe.sources = populated_objs
        # Print debugging info
        print(f"found {len(current_obj.recipes)} recipes")
        if len(current_obj.recipes):
            recipe = current_obj.recipes[0]
            print(f"rate_amount={recipe.rate_amount}")
            print(f"instant={recipe.instant}")
            for source_item, source_qty in recipe.sources.items():
                print(f"item id {source_item.id} name {source_item.name} qty {source_qty}")
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
                self.progress.quantity = int(request.form.get('item_quantity'))
            self.progress = Progress.from_json({
                'quantity': self.progress.quantity,
                'q_limit': int(request.form.get('item_limit'))})
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
                    source_quantity = int(request.form.get(
                        f'recipe_{recipe_id}_source_{source_id}_quantity', 0))
                    source_item = Item(source_id)
                    recipe.sources[source_item] = source_quantity
                    print(f"Sources for {recipe_id}: ", {source.id: quantity
                        for source, quantity in recipe.sources.items()})
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
        item = Item.get_by_id(item_id)
        if item:
            return render_template(
                'play/item.html',
                current=item,
                game_data=g.game_data)
        else:
            return 'Item not found'

    @app.route('/item/gain/<int:item_id>', methods=['POST'])
    def gain_item(item_id):
        quantity = int(request.form.get('quantity'))
        item = Item.get_by_id(item_id)
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
        item = Item.from_db(item_id)
        if item:
            if item.progress.is_ongoing:
                item.progress.determine_current_quantity()
            return jsonify({
                'is_ongoing': item.progress.is_ongoing,
                'quantity': item.progress.quantity,
                'elapsed_time': item.progress.calculate_elapsed_time()})
        else:
            return jsonify({'error': 'Item not found'})

    @app.route('/item/start/<int:item_id>')
    def start_item(item_id):
        item = Item.get_by_id(item_id)
        if item.progress.start():
            item.to_db()
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/item/stop/<int:item_id>')
    def stop_item(item_id):
        item = Item.get_by_id(item_id)
        if item.progress.stop():
            item.to_db()
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})
