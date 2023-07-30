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
from .db_serializable import Identifiable, coldef, load_game_data
from database import column_counts

tables_to_create = {
    'items': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        growable boolean,
        result_qty float(2) NOT NULL,
        progress_id integer,
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """
}

class Item(Identifiable):
    def __init__(self, id=""):
        super().__init__(id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.get_list()) > 1 else True
        self.attribs = {}  # keys are Attrib object, values are stat val
        self.progress = Progress(self)
        self.growable = 'over_time'
        self.sources = {}  # keys Item object, values quantity required
        self.result_qty = 1  # how many one batch yields

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'growable': self.growable,
            'result_qty': self.result_qty,
            'sources': {
                str(item.id): quantity
                for item, quantity in self.sources.items()},
            'attribs': {
                str(attrib.id): val
                for attrib, val in self.attribs.items()},
            'progress': self.progress.to_json(),
        }

    @classmethod
    def from_json(cls, data, id_refs):
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        instance.toplevel = data['toplevel']
        instance.growable = data['growable']
        instance.result_qty = data.get('result_qty', 1)
        instance.progress = Progress.from_json(data['progress'], instance)
        instance.progress.step_size = instance.result_qty
        id_refs.setdefault('source', {})[instance.id] = {
            int(source_id): quantity
            for source_id, quantity in data['sources'].items()}
        instance.attribs = {
            Attrib.get_by_id(int(attrib_id)): val
            for attrib_id, val in data['attribs'].items()}
        return instance

    def to_db(self):
        super().to_db()
        progress_data = self.to_json()['progress']
        super().json_to_db(progress_data)

    @classmethod
    def list_with_references(cls, callback):
        id_refs = {}
        callback(id_refs)
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        entity_list = cls.get_list()
        for instance in entity_list:
            instance.sources = {
                cls.get_by_id(source_id): quantity
                for source_id, quantity in
                id_refs.get('source', {}).get(instance.id, {}).items()}
            instance.progress.sources = instance.sources
        return entity_list

    @classmethod
    def list_from_json(cls, json_data):
        def callback(id_refs):
            super(cls, cls).list_from_json(json_data, id_refs)
        return cls.list_with_references(callback)

    @classmethod
    def list_from_db(cls):
        def callback(id_refs):
            super(cls, cls).list_from_db(id_refs)
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
            instances = [cls.from_json(vars(dat), id_refs) for dat in data]
            return instances
        return cls.list_with_references(callback)

    @classmethod
    def data_for_configure(cls, item_id):
        print(f"{cls.__name__}.data_for_configure()")
        query = """
            SELECT *
            FROM items
            WHERE game_token = %s
            LEFT JOIN progress
                ON items.progress_id = progress.id
                AND items.game_token = progress.game_token
                AND items.id = %s
        """
        game_data = GameData()
        cursor.execute(query, (g.game_token, item_id))
        rows = cursor.fetchall()
        items_data = []
        column_names = [desc[0] for desc in cursor.description]
        items_cols = column_counts('items')
        progress_data = None
        current_item_data = None
        for row in rows:
            item_data = dict(zip(
                column_names[:items_cols], row[:items_cols]))
            if item_data.id == item_id:
                current_item_data = item_data
                progress_data = dict(zip(
                    column_names[items_cols:], row[items_cols:]))
                item_data['progress'] = progress_data
            items_data.append(item_data)
            game_data.items.append(Item.from_json(item_data))
        if 'progress' not in current_item_data:
            raise Exception(
                f"Did not find progress data for item {current_item_data.id}.")
        query = """
            SELECT *
            FROM attribs
            WHERE game_token = %s
            LEFT JOIN item_attribs ON
                ON attribs.id = item_attribs.attrib_id
                AND attribs.game_token = item_attribs.game_token
                AND item_attribs.item_id = %s
        """
        cursor.execute(query, (g.game_token, item_id))
        rows = cursor.fetchall()
        attribs_data = []
        column_names = [desc[0] for desc in cursor.description]
        attribs_cols = column_counts('items')
        for row in rows:
            attrib_data = dict(zip(
                column_names[:attribs_cols], row[:attribs_cols]))
            if row[attribs_cols]:
                item_attribs_data = dict(zip(
                    column_names[attribs_cols:], row[attribs_cols:]))
                current_item_data.setdefault('attribs', []).append(
                    item_attribs_data)
            items_data.append(item_data)
            game_data.attribs.append(Attrib.from_json(attrib_data))
        sources_data = DbSerializable.execute_select(f"""
            SELECT *
            FROM item_sources
            WHERE game_token = %s
                AND item_id = %s
        """, (g.game_token, item_id))
        for row in sources_data:
            current_item_data.setdefault('sources', []).append(row)
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
            instance.growable = request.form.get('growable')
            instance.result_qty = float(request.form.get('result_quantity'))
            source_ids = request.form.getlist('source_id')
            print(f"Source IDs: {source_ids}")
            instance.sources = {}
            for source_id in source_ids:
                source_quantity = float(
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
                attrib_item = Attrib.get_by_id(attrib_id)
                instance.attribs[attrib_item] = attrib_val
            print("attribs: ", {attrib.name: val
                for attrib, val in instance.attribs.items()})
            was_ongoing = instance.progress.is_ongoing
            if was_ongoing:
                instance.progress.stop()
            else:
                instance.progress.quantity = float(
                    request.form.get('item_quantity'))
            prev_quantity = instance.progress.quantity
            instance.progress = Progress(
                instance,
                quantity=prev_quantity,
                step_size=instance.result_qty,
                rate_amount=float(request.form.get('rate_amount')),
                rate_duration=float(request.form.get('rate_duration')),
                sources=instance.sources)
            instance.progress.q_limit = float(request.form.get('item_limit'))
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
        quantity = float(request.form.get('quantity'))
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
