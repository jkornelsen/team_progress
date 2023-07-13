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
from .item import Item
from .location import Location
from .progress import Progress

class Character:
    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class
    game_data = None

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
        self.items = {}  # keys are Item object, values are slot name
        self.location = None  # Location object
        self.progress = Progress()  # for travel or perhaps other actions
        self.destination = None  # Location object to travel to
        self.user_id = ""

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
            'items': {item.id: slot for item, slot in self.items.items()},
            'attribs': {
                attrib.id: val
                for attrib, val in self.attribs.items()},
            'location_id': self.location.id if self.location else None,
            'progress': self.progress.to_json(),
            'dest_id': self.destination.id if self.destination else None
        }

    @classmethod
    def from_json(cls, data):
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        instance.toplevel = data['toplevel']
        instance.items = {
            Item.get_by_id(int(item_id)): slot
            for item_id, slot in data['items'].items()}
        instance.attribs = {
            Attrib.get_by_id(int(attrib_id)): val
            for attrib_id, val in data['attribs'].items()}
        instance.location = Location.get_by_id(
            int(data['location_id'])) if data['location_id'] else None
        instance.progress = Progress.from_json(data['progress'])
        cls.instances.append(instance)
        return instance

    @classmethod
    def list_from_json(cls, json_data):
        cls.instances.clear()
        for char_data in json_data:
            cls.from_json(char_data)
        cls.last_id = max(
            (char.id for char in cls.instances), default=0)
        return cls.instances

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
                self.name = request.form.get('char_name')
                self.description = request.form.get('char_description')
                self.toplevel = bool(request.form.get('top_level'))
                item_ids = request.form.getlist('item_id[]')
                item_slots = request.form.getlist('item_slot[]')
                self.items = {}
                for item_id, item_slot in zip(item_ids, item_slots):
                    item = Item.get_by_id(int(item_id))
                    if item:
                        self.items[item] = item_slot
                location_id = request.form.get('char_location')
                self.location = Location.get_by_id(
                    int(location_id)) if location_id else None
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
            elif 'delete_character' in request.form:
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
            return render_template(
                'configure/character.html',
                current=self, current_user_id=g.user_id)

def set_routes(app):
    @app.route('/configure/char/<char_id>', methods=['GET', 'POST'])
    def configure_char(char_id):
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_char(): {request.referrer}")
        if char_id == "new":
            print("Creating a new character.")
            char = Character()
        else:
            print(f"Retrieving character with ID: {char_id}")
            char = Character.get_by_id(int(char_id))
        return char.configure_by_form()

    @app.route('/play/char/<int:char_id>')
    def play_char(char_id):
        char = Character.get_by_id(char_id)
        if char:
            return render_template(
                'play/character.html',
                current=char, current_user_id=g.user_id)
        else:
            return 'Character not found'

    @app.route('/char/start/<int:char_id>', methods=['POST'])
    def start_char(char_id):
        dest_id = int(request.form.get('dest_id'))
        char = Character.get_by_id(char_id)
        if not char.destination or char.destination.id != dest_id:
            char.destination = Location.get_by_id(dest_id)
            char.progress.quantity = 0
        try:
            char.progress.can_change_quantity(char.progress.rate_amount)
        except Exception as ex:
            return jsonify({'status': 'error', 'message': str(ex)})
        if char.progress.start():
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/char/stop/<int:char_id>')
    def stop_char(char_id):
        char = Character.get_by_id(char_id)
        if char.progress.stop():
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})

    @app.route('/char/progress_running/<int:char_id>')
    def char_progress_running(char_id):
        char = Character.get_by_id(char_id)
        return jsonify({'is_running': char.progress.is_running})

    @app.route('/char/progress_quantity/<int:char_id>')
    def char_progress_quantity(char_id):
        char = Character.get_by_id(char_id)
        if char:
            if not char.location or not char.destination:
                #return jsonify({'error': 'No travel destination.'})
                return jsonify({'quantity': 0})
            distance = char.location.destinations[char.destination]
            if char.progress.quantity >= distance:
                # arrived at the destination
                char.progress.stop()
                char.progress.quantity = 0
                char.location = char.destination
                char.destination = None
                return jsonify({'status': 'arrived'})
            else:
                return jsonify({'quantity': int(char.progress.quantity)})
        else:
            return jsonify({'error': 'Character not found.'})

