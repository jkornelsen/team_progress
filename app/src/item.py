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
from .db_serializable import Identifiable, coldef

tables_to_create = {
    'item': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        growable BOOLEAN,
        result_qty FLOAT(2) NOT NULL,
        progress_id INTEGER,
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """,
    'item_attribs': f"""
        {coldef('token')},
        item_id INTEGER PRIMARY KEY,
        attrib_id INTEGER PRIMARY KEY,
        FOREIGN KEY (game_token, item_id)
            REFERENCES item (game_token, id),
        FOREIGN KEY (game_token, attrib_id)
            REFERENCES attrib (game_token, id)
    """,
    'item_sources': f"""
        {coldef('token')},
        item_id INTEGER PRIMARY KEY,
        source_id INTEGER PRIMARY KEY,
        FOREIGN KEY (game_token, item_id)
            REFERENCES item (game_token, id),
        FOREIGN KEY (game_token, source_id)
            REFERENCES item (game_token, id)
    """
}

class Item(Identifiable):
    def __init__(self, id=""):
        super().__init__(id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.instances) > 1 else True
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
        cls.instances.append(instance)
        return instance

    @classmethod
    def list_with_references(cls, callback):
        id_refs = {}
        callback(id_refs)
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        for instance in cls.instances:
            instance.sources = {
                cls.get_by_id(source_id): quantity
                for source_id, quantity in
                id_refs.get('source', {}).get(instance.id, {}).items()}
            instance.progress.sources = instance.sources
        return cls.instances

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

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.instances:
                    self.instances.append(self)
                self.name = request.form.get('item_name')
                self.description = request.form.get('item_description')
                self.toplevel = bool(request.form.get('top_level'))
                self.growable = request.form.get('growable')
                self.result_qty = int(request.form.get('result_quantity'))
                source_ids = request.form.getlist('source_id')
                print(f"Source IDs: {source_ids}")
                self.sources = {}
                for source_id in source_ids:
                    source_quantity = int(
                        request.form.get(f'source_quantity_{source_id}', 0))
                    source_item = self.get_by_id(source_id)
                    self.sources[source_item] = source_quantity
                print("Sources: ", {source.name: quantity
                    for source, quantity in self.sources.items()})
                attrib_ids = request.form.getlist('attrib_id')
                print(f"Attrib IDs: {attrib_ids}")
                self.attribs = {}
                for attrib_id in attrib_ids:
                    attrib_val = int(
                        request.form.get(f'attrib_val_{attrib_id}', 0))
                    attrib_item = Attrib.get_by_id(attrib_id)
                    self.attribs[attrib_item] = attrib_val
                print("attribs: ", {attrib.name: val
                    for attrib, val in self.attribs.items()})
                was_ongoing = self.progress.is_ongoing
                if was_ongoing:
                    self.progress.stop()
                else:
                    self.progress.quantity = int(request.form.get('item_quantity'))
                prev_quantity = self.progress.quantity
                self.progress = Progress(
                    self,
                    quantity=prev_quantity,
                    step_size=self.result_qty,
                    rate_amount=float(request.form.get('rate_amount')),
                    rate_duration=float(request.form.get('rate_duration')),
                    sources=self.sources)
                self.progress.limit = int(request.form.get('item_limit'))
                if was_ongoing:
                    self.progress.start()
                self.to_db()
            elif 'delete_item' in request.form:
                self.instances.remove(self)
                self.remove_from_db(self.id)
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
        else:
            return render_template(
                'configure/item.html',
                current=self,
                game_data=g.game_data)

def set_routes(app):
    @app.route('/configure/item/<item_id>', methods=['GET', 'POST'])
    def configure_item(item_id):
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_item(): {request.referrer}")
        if item_id == "new":
            print("Creating a new item.")
            item = Item()
        else:
            print(f"Retrieving item with ID: {item_id}")
            item = Item.get_by_id(int(item_id))
        return item.configure_by_form()

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
