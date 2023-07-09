from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from .timer import Timer

class Item:
    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class
    game_data = None

    def __init__(self, new_id='auto'):
        if new_id == 'auto':
            Item.last_id += 1
            self.id = Item.last_id
        else:
            self.id = new_id
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.__class__.instances) > 1 else True
        self.timer = Timer(self)
        self.growable = 'over_time'
        self.sources = {}  # keys Item object, values quantity required
        self.result_qty = 1  # how many one batch yields
        self.contained_by = None  # Location or Character object

    @classmethod
    def get_by_id(cls, id_to_get):
        id_to_get = int(id_to_get)
        return next(
            (instance for instance in cls.instances
            if instance.id == id_to_get), None)

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
            'timer': self.timer.to_json(),
        }

    @classmethod
    def from_json(cls, data, source_ids):
        item = cls(int(data['id']))
        item.name = data['name']
        item.description = data.get('description', '')
        item.toplevel = data['toplevel']
        item.growable = data['growable']
        item.result_qty = data.get('result_qty', 1)
        item.timer = Timer.from_json(data['timer'], item)
        source_ids[item.id] = {
            int(source_id): quantity
            for source_id, quantity in data['sources'].items()
        }
        cls.instances.append(item)
        return item

    @classmethod
    def item_list_from_json(cls, json_data):
        cls.instances.clear()
        source_ids = {}
        for item_data in json_data:
            cls.from_json(item_data, source_ids)
        cls.last_id = max(
            (item.id for item in cls.instances), default=0)
        # set the source item objects now that all items have been loaded
        for item in cls.instances:
            item.sources = {
                cls.get_by_id(source_id): quantity
                for source_id, quantity in
                source_ids.get(item.id, {}).items()
            }
        return cls.instances

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
                self.name = request.form.get('item_name')
                self.description = request.form.get('item_description')
                self.toplevel = bool(request.form.get('top_level'))
                self.growable = request.form.get('growable')
                rate_amount = float(request.form.get('rate_amount'))
                rate_duration = float(request.form.get('rate_duration'))
                was_running = self.timer.is_running
                if was_running:
                    self.timer.stop()
                self.timer = Timer(
                    self, rate_amount, rate_duration, self.timer.quantity)
                self.result_qty = int(request.form.get('result_quantity'))
                source_ids = request.form.getlist('source_id')
                print(f"Source IDs: {source_ids}")
                self.sources = {}
                for source_id in source_ids:
                    source_quantity = int(
                        request.form.get(f'source_quantity_{source_id}', 0))
                    source_item = self.__class__.get_by_id(source_id)
                    self.sources[source_item] = source_quantity
                print(f"Source Quantities: {self.sources}")
                if was_running:
                    self.timer.start()
                print(request.form)
            elif 'delete_item' in request.form:
                self.__class__.instances.remove(self)
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
            return render_template('configure/item.html', current=self)

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
            return render_template('play/item.html', current=item)
        else:
            return 'Item not found'

    @app.route('/item/gain/<int:item_id>', methods=['POST'])
    def gain_item(item_id):
        quantity = int(request.form.get('quantity'))
        item = Item.get_by_id(item_id)
        try:
            item.timer.can_change_quantity(quantity)
        except Exception as ex:
            return jsonify({'status': 'error', 'message': str(ex)})
        if item.timer.change_quantity(quantity):
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
            item.timer.can_change_quantity(item.timer.rate_amount)
        except Exception as ex:
            return jsonify({'status': 'error', 'message': str(ex)})
        if item.timer.start():
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/item/stop/<int:item_id>')
    def stop_item(item_id):
        item = Item.get_by_id(item_id)
        if item.timer.stop():
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})

    @app.route('/item/timer_status/<int:item_id>')
    def item_timer_status(item_id):
        item = Item.get_by_id(item_id)
        return jsonify({'is_running': item.timer.is_running})

    @app.route('/item/quantity/<int:item_id>')
    def get_quantity(item_id):
        item = Item.get_by_id(item_id)
        if item:
            return jsonify({'quantity': item.timer.quantity})
        else:
            return jsonify({'error': 'Item not found'})

    @app.route('/item/time/<int:item_id>')
    def get_time(item_id):
        item = Item.get_by_id(item_id)
        if item:
            return item.timer.get_time()
        else:
            return jsonify({'error': 'Item not found'})

