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
from .db_serializable import DbSerializable, Identifiable, coldef, new_game_data

tables_to_create = {
    'items': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        producible boolean,
        progress_id integer,
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """
}

class Recipe:
    def __init__(self):
        self.id = 0
        self.instant = False
        self.rate_amount = 1  # quantity produced per batch
        self.rate_duration = 1.0  # seconds for a batch
        self.sources = {}  # Items and their quantity

class Item(Identifiable):
    def __init__(self, id=""):
        super().__init__(id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.get_list()) > 1 else True
        self.attribs = {}  # keys are Attrib object, values are stat val
        self.recipes = {}  # Recipe IDs and their objects
        self.progress = Progress(self)

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'recipes': {
                str(recipe_id): {
                    'instant': recipe.instant,
                    'rate_amount': recipe.rate_amount,
                    'rate_duration': recipe.duration,
                    'sources': {
                        str(item.id): quantity
                        for item, quantity in recipe.sources.items()}}
                for recipe_id, recipe in self.recipes.items()},
            'attribs': {
                str(attrib.id): val
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
        instance.recipes = {}
        for recipe_id, recipe in data.get('recipes', {}).items():
            recipe = Recipe()
            recipe.instant = recipe.get('instant', False),
            recipe.rate_amount = recipe.get('rate_amount', 1),
            recipe.rate_duration = recipe.get('duration', 1.0),
            recipe.sources = {
                int(source_id): quantity
                for source_id, quantity in recipe.get('sources', {}).items()}
            instance.recipes[recipe_id] = recipe
        return instance

    def to_db(self):
        super().to_db()
        progress_data = self.to_json()['progress']
        self.progress.json_to_db(progress_data)
        # TODO: populate item_attribs and item_sources

    @classmethod
    def list_with_references(cls, json_data=None):
        if json_data:
            super(cls, cls).list_from_json(json_data)
        else:
            instances = super(cls, cls).list_from_db()
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        entity_list = cls.get_list()
        for instance in entity_list:
            instance.sources = [
                {cls.get_by_id(source_id): quantity
                for source_id, quantity in recipe.items()}
                for recipe in id_refs.get('sources', {}).get(instance.id, {})]
            instance.progress.sources = instance.sources
        return entity_list

    @classmethod
    def list_from_json(cls, json_data):
        return cls.list_with_references(json_data)

    @classmethod
    def list_from_db(cls):
        return cls.list_with_references(callback)

    @classmethod
    def list_from_db_with_rels(cls):
        print(f"{cls.__name__}.list_from_db()")
        def callback(id_refs):
            query = """
                SELECT *
                FROM items
                JOIN progress ON items.progress_id = progress.id
                    AND items.game_token = progress.game_token
            """
            cursor.execute(query, (game_token,))
            rows = cursor.fetchall()
            items = []
            column_names = [desc[0] for desc in cursor.description]
            items_cols = column_counts('items')
            for row in rows:
                item_data = dict(zip(
                    column_names[:items_cols], row[:items_cols]))
                progress_data = dict(zip(
                    column_names[items_cols:], row[items_cols:]))
            table = cls.tablename()
            data = DbSerializable.execute_select(f"""
                SELECT *
                FROM {table}
                WHERE game_token = %s
            """, (g.game_token,))
            instances = [cls.from_json(dat, id_refs) for dat in data]
            return instances
        return cls.list_with_references(callback)

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
        current_item_data = {}
        for item_data, progress_data in tables_rows:
            if item_data.id == item_id:
                current_item_data = item_data
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
        attribs_data = []
        for attrib_data, item_attrib_data in tables_rows:
            print(f"attrib_data={attrib_data}, item_attrib_data={item_attrib_data}")
            if item_attrib_data.attrib_id:
                current_item_data.setdefault('attribs', []).append(
                    item_attrib_data)
            g.game_data.attribs.append(Attrib.from_json(attrib_data))
        recipes_data = DbSerializable.execute_select("""
            SELECT *
            FROM item_sources
            WHERE game_token = %s
                AND item_id = %s
        """, (g.game_token, item_id))
        for row in recipes_data:
            current_item_data.setdefault('recipes', []).append(row)
        current_item = Item.from_json(current_item_data)
        return current_item

    @classmethod
    def configure_by_form(cls, item_id):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            entity_list = cls.get_list()
            instance = Item(item_id)
            if instance not in entity_list:
                entity_list.append(instance)
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
            attrib_ids = request.form.getlist('attrib_id')
            print(f"Attrib IDs: {attrib_ids}")
            instance.attribs = {}
            for attrib_id in attrib_ids:
                attrib_val = int(
                    request.form.get(f'attrib_val_{attrib_id}', 0))
                attrib_item = Attrib(attrib_id)
                instance.attribs[attrib_item] = attrib_val
            print("attribs: ", {attrib.id: val
                for attrib, val in instance.attribs.items()})
            was_ongoing = instance.progress.is_ongoing
            if was_ongoing:
                instance.progress.stop()
            else:
                instance.progress.quantity = int(
                    request.form.get('item_quantity'))
            prev_quantity = instance.progress.quantity
            instance.progress = Progress.from_json({
                    'quantity': prev_quantity,
                    'rate_amount': int(request.form.get('rate_amount')),
                    'rate_duration': int(request.form.get('rate_duration')),
                    'sources': instance.sources},
                instance)
            instance.progress.q_limit = int(request.form.get('item_limit'))
            if was_ongoing:
                instance.progress.start()
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
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_item(): {request.referrer}")
            item = Item.data_for_configure(item_id)
            return render_template(
                'configure/item.html',
                current=item,
                game_data=g.game_data)
        else:
            return Item.configure_by_form(item_id)

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
        item = Item.get_by_id(item_id)
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
