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
    DbSerializable, Identifiable, MutableNamespace, coldef, new_game_data)

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
        self.id = new_id
        self.instant = False
        self.rate_amount = 1  # quantity produced per batch
        self.rate_duration = 1.0  # seconds for a batch
        self.sources = {}  # Items and their quantity

    def to_json(self):
        return {
            'id': self.id,
            'instant': self.instant,
            'rate_amount': self.rate_amount,
            'rate_duration': self.duration,
            'sources': {
                str(item.id): quantity
                for item, quantity in self.sources.items()}}

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.instant = data.get('instant', False),
        instance.rate_amount = data.get('rate_amount', 1),
        instance.rate_duration = data.get('duration', 1.0),
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
        self.recipes = []
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
            values = []
            for recipe_id, recipe in enumerate(doc['recipes']):
                for source_id, src_qty in recipe['sources'].items():
                    values.append((
                        g.game_token, item_id, recipe_id, source_id,
                        src_qty,
                        recipe.rate_amount,
                        recipe.rate_duration,
                        recipe.instant
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
        tables_rows = DbSerializable.select_tables(
            query, values,
            ['items', 'progress', 'item_attribs', 'item_sources'])
        instances = {}  # keyed by ID
        for item_data, progress_data, attribs_data, source_data in tables_rows:
            instance = instances.get(item_data.id)
            if not instance:
                instance = cls.from_json(vars(item_data))
                instances[item_data.id] = instance
            if progress_data.id:
                instance.progress = Progress.from_json(progress_data, instance)
            if attribs_data.attrib_id:
                instance.attribs[attribs_data.attrib_id] = attribs_data.value
            if source_data.recipe_id:
                if (recipe := instance.recipes.get(source_data.recipe_id)) is None:
                    recipe = Recipe.from_json(source_data)
                    instance.recipes[source_data.recipe_id] = recipe
                recipe.sources[source_data.source_id] = source_data.src_qty
        # replace IDs with partial objects
        for instance in instances.values():
            attrib_objs = {}
            for attrib_id in instance.attribs:
                attrib_obj = Attrib(attrib_id)
                attrib_objs[attrib_obj] = instance.attribs[attrib_id]
            instance.attribs = attrib_objs
        instances = list(instances.values())
        return instances[0] if len(instances) == 1 else instances

    @classmethod
    def list_with_references(cls, json_data=None):
        """Replace ID references or partial objects with filled objects 
        using cls.get_by_id()."""
        #if json_data:
        #    super(cls, cls).list_from_json(json_data)
        #else:
        #    instances = super(cls, cls).list_from_db()
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        #entity_list = cls.get_list()
        #for instance in entity_list:
        #    instance.sources = [
        #        {cls.get_by_id(source_id): quantity
        #        for source_id, quantity in recipe.items()}
        #        for recipe in id_refs.get('sources', {}).get(instance.id, {})]
        #    instance.progress.sources = instance.sources
        #return entity_list
        raise NotImplementedError()

    @classmethod
    def data_for_configure(cls, item_id):
        print(f"{cls.__name__}.data_for_configure()")
        if item_id == 'new':
            item_id = 0
        else:
            item_id = int(item_id)
        tables_rows = DbSerializable.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[0]}.progress_id = {tables[1]}.id
                AND {tables[0]}.game_token = {tables[1]}.game_token
                AND {tables[0]}.id = %s
            WHERE {tables[0]}.game_token = %s
        """, (item_id, g.game_token), ['items', 'progress'])
        g.game_data.items = []
        current_data = MutableNamespace()
        for item_data, progress_data in tables_rows:
            print(f"item_data={item_data}")
            if item_data.id == item_id:
                current_data = item_data
                if progress_data.id:
                    item_data.progress = progress_data
            g.game_data.items.append(Item.from_json(item_data))
        tables_rows = DbSerializable.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[0]}.id = {tables[1]}.attrib_id
                AND {tables[0]}.game_token = {tables[1]}.game_token
                AND {tables[1]}.item_id = %s
            WHERE {tables[0]}.game_token = %s
        """, (item_id, g.game_token), ['attribs', 'item_attribs'])
        for attrib_data, item_attrib_data in tables_rows:
            print(f"attrib_data={attrib_data}, item_attrib_data={item_attrib_data}")
            if item_attrib_data.attrib_id:
                current_data.setdefault(
                    'attribs', {})[attrib_data.id] = item_attrib_data.value
                print(f"current_data.attribs={current_data.attribs}")
            g.game_data.attribs.append(Attrib.from_json(attrib_data))
        recipes_data = DbSerializable.execute_select("""
            SELECT *
            FROM item_sources
            WHERE game_token = %s
                AND item_id = %s
        """, (g.game_token, item_id))
        recipes_data = {}
        for row in recipes_data:
            if row.recipe_id in recipes_data:
                recipe_data = recipes_data[row.recipe_id]
            else:
                recipe_data = row
            recipe_data.get('sources', []).append({row.source_id: row.src_qty})
        current_data.recipes = recipes_data
        current_item = Item.from_json(current_data)
        # replace partial objects with fully populated objects
        populated_objs = {}
        for partial_attrib, val in current_item.attribs.items():
            attrib = Attrib.get_by_id(partial_attrib.id)
            populated_objs[attrib] = val
        current_item.attribs = populated_objs
        return current_item

    @classmethod
    def configure_by_form(cls, old_instance):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            if old_instance.progress.is_ongoing:
                old_instance.progress.stop()
                item_qty = old_instance.quantity
            else:
                item_qty = int(request.form.get('item_quantity'))
            instance = Item(old_instance.id)
            instance.name = request.form.get('item_name')
            instance.description = request.form.get('item_description')
            instance.toplevel = bool(request.form.get('top_level'))
            instance.instant = bool(request.form.get('instant'))
            #instance.result_qty = int(request.form.get('result_quantity'))
            source_ids = request.form.getlist('source_id')
            print(f"Source IDs: {source_ids}")
            instance.sources = {}
            for source_id in source_ids:
                source_quantity = int(
                    request.form.get(f'source_quantity_{source_id}', 0))
                source_item = instance.get_by_id(source_id)
                instance.sources[source_item] = source_quantity
            print("Sources: ", {source.name: quantity
                for source, quantity in instance.sources.items()})
            #'rate_amount': int(request.form.get('rate_amount')),
            #'rate_duration': int(request.form.get('rate_duration')),
            attrib_ids = request.form.getlist('attrib_id')
            print(f"Attrib IDs: {attrib_ids}")
            instance.attribs = {}
            for attrib_id in attrib_ids:
                attrib_val = int(
                    request.form.get(f'attrib_val_{attrib_id}', 0))
                attrib_obj = Attrib(attrib_id)
                instance.attribs[attrib_obj] = attrib_val
            print("attribs: ", {attrib.id: val
                for attrib, val in instance.attribs.items()})
            instance.progress = Progress.from_json({
                'quantity': item_qty,
                'q_limit': int(request.form.get('item_limit'))})
            instance.to_db()
        elif 'delete_item' in request.form:
            instance.remove_from_db(instance.id)
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
        item = Item.data_for_configure(item_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_item(): {request.referrer}")
            return render_template(
                'configure/item.html',
                current=item,
                game_data=g.game_data)
        else:
            return Item.configure_by_form(item)

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
