from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from .attrib import Attrib
from .progress import Progress
from .db_serializable import DbSerializable

class Item(DbSerializable):
    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class

    def __init__(self, new_id='auto'):
        if new_id == 'auto':
            self.__class__.last_id += 1
            self.id = self.__class__.last_id
        else:
            self.id = new_id
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.__class__.instances) > 1 else True
        self.attribs = {}  # keys are Attrib object, values are stat val
        self.progress = Progress()
        self.growable = 'over_time'
        self.sources = {}  # keys Item object, values quantity required
        self.result_qty = 1  # how many one batch yields
        self.user_id = ""  # whoever last played with this item

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'growable': self.growable,
            'result_qty': self.result_qty,
            'sources': {
                item.id: quantity
                for item, quantity in self.sources.items()},
            'attribs': {
                attrib.id: val
                for attrib, val in self.attribs.items()},
            'progress': self.progress.to_json(),
        }

    @classmethod
    def from_json(cls, data):
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        instance.toplevel = data['toplevel']
        instance.growable = data['growable']
        instance.result_qty = data.get('result_qty', 1)
        instance.progress = Progress.from_json(data['progress'])
        instance.progress.step_size = instance.result_qty
        cls.source_ids[instance.id] = {
            int(source_id): quantity
            for source_id, quantity in data['sources'].items()}
        instance.attribs = {
            Attrib.get_by_id(int(attrib_id)): val
            for attrib_id, val in data['attribs'].items()}
        cls.instances.append(instance)
        return instance

    @classmethod
    def list_with_references(cls, callback):
        cls.source_ids = {}
        cls.attrib_ids = {}
        callback()
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        for instance in cls.instances:
            instance.sources = {
                cls.get_by_id(source_id): quantity
                for source_id, quantity
                in cls.source_ids.get(instance.id, {}).items()}
            instance.progress.sources = instance.sources
        del cls.source_ids  # remove attr from class
        del cls.attrib_ids  # remove attr from class
        return cls.instances

    @classmethod
    def list_from_json(cls, json_data):
        def callback():
            super().list_from_json(json_data)
        return cls.list_with_references(callback)

    @classmethod
    def list_from_db(cls):
        def callback():
            super().list_from_db()
        return cls.list_with_references(callback)

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
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
                    source_item = self.__class__.get_by_id(source_id)
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
                was_running = self.progress.is_running
                if was_running:
                    self.progress.stop()
                else:
                    self.progress.quantity = int(request.form.get('item_quantity'))
                prev_quantity = self.progress.quantity
                self.progress = Progress(
                    quantity=prev_quantity,
                    step_size=self.result_qty,
                    rate_amount=float(request.form.get('rate_amount')),
                    rate_duration=float(request.form.get('rate_duration')),
                    sources=self.sources)
                self.progress.limit = int(request.form.get('item_limit'))
                if was_running:
                    self.progress.start()
                self.to_db()
            elif 'delete_item' in request.form:
                self.__class__.instances.remove(self)
                self.__class__.remove_from_db(self.id)
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
                current_user_id=g.user_id,
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
                current_user_id=g.user_id,
                game_data=g.game_data)
        else:
            return 'Item not found'

    @app.route('/item/gain/<int:item_id>', methods=['POST'])
    def gain_item(item_id):
        quantity = int(request.form.get('quantity'))
        item = Item.get_by_id(item_id)
        try:
            item.progress.can_change_quantity(quantity)
        except Exception as ex:
            return jsonify({'status': 'error', 'message': str(ex)})
        if item.progress.change_quantity(quantity):
            return jsonify({
                'status': 'success', 'message':
                f'Quantity of {item.name} changed by {quantity}.'})
        else:
            return jsonify({
                'status': 'error',
                'message': 'Could not change quantity.'})

    @app.route('/item/start/<int:item_id>')
    def start_item(item_id):
        item = Item.get_by_id(item_id)
        try:
            item.progress.can_change_quantity(item.progress.rate_amount)
        except Exception as ex:
            return jsonify({'status': 'error', 'message': str(ex)})
        if item.progress.start():
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/item/stop/<int:item_id>')
    def stop_item(item_id):
        item = Item.get_by_id(item_id)
        if item.progress.stop():
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})

    @app.route('/item/progress_running/<int:item_id>')
    def item_progress_running(item_id):
        item = Item.get_by_id(item_id)
        return jsonify({'is_running': item.progress.is_running})

    @app.route('/item/progress_quantity/<int:item_id>')
    def item_progress_quantity(item_id):
        item = Item.get_by_id(item_id)
        if item:
            return jsonify({'quantity': item.progress.quantity})
        else:
            return jsonify({'error': 'Item not found'})

    @app.route('/item/progress_time/<int:item_id>')
    def item_progress_time(item_id):
        item = Item.get_by_id(item_id)
        if item:
            return item.progress.get_time()
        else:
            return jsonify({'error': 'Item not found'})

