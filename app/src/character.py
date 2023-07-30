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
from .db_serializable import Identifiable, coldef
from .attrib import Attrib
from .item import Item
from .location import Location
from .progress import Progress

tables_to_create = {
    'characters': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        location_id integer,
        progress_id integer,
        destination_id integer,
        position integer[2]
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """,
}

class Character(Identifiable):
    def __init__(self, id=""):
        super().__init__(id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.get_list()) > 1 else True
        self.attribs = {}  # keys are Attrib object, values are stat val
        self.items = {}  # Item objects and their slot name
        self.location = None  # Location object
        self.progress = Progress(self)  # for travel or perhaps other actions
        self.destination = None  # Location object to travel to

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'attribs': {
                str(attrib.id): val
                for attrib, val in self.attribs.items()},
            'items': {
                str(item.id): slot
                for item, slot in self.items.items()},
            'location_id': self.location.id if self.location else None,
            'progress': self.progress.to_json(),
            'dest_id': self.destination.id if self.destination else None
        }

    @classmethod
    def from_json(cls, data, _):
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
        instance.progress = Progress.from_json(data['progress'], instance)
        instance.destination = Location.get_by_id(
            int(data['dest_id'])) if data['dest_id'] else None
        return instance

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                entity_list = self.get_list()
                if self not in entity_list:
                    entity_list.append(self)
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
                self.to_db()
            elif 'delete_character' in request.form:
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
                'configure/character.html',
                current=self,
                game_data=g.game_data)

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
                current=char)
        else:
            return 'Character not found'

    @app.route('/char/start/<int:char_id>', methods=['POST'])
    def start_char(char_id):
        dest_id = int(request.form.get('dest_id'))
        char = Character.get_by_id(char_id)
        if not char.destination or char.destination.id != dest_id:
            char.destination = Location.get_by_id(dest_id)
            char.progress.quantity = 0
            char.to_db()
        if char.progress.start():
            char.to_db()
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/char/stop/<int:char_id>')
    def stop_char(char_id):
        char = Character.get_by_id(char_id)
        if char.progress.stop():
            char.to_db()
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})

    @app.route('/char/progress_data/<int:char_id>')
    def char_progress_data(char_id):
        char = Character.get_by_id(char_id)
        if char:
            if not char.location or not char.destination:
                return jsonify({
                    'quantity': 0,
                    'is_ongoing': False,
                    'message': 'No travel destination.'})
            if char.progress.is_ongoing:
                char.progress.determine_current_quantity()
                char.to_db()
            distance = char.location.destinations[char.destination]
            if char.progress.quantity >= distance:
                # arrived at the destination
                char.progress.stop()
                char.progress.quantity = 0
                char.location = char.destination
                char.destination = None
                char.to_db()
                return jsonify({'status': 'arrived'})
            else:
                return jsonify({
                    'is_ongoing': char.progress.is_ongoing,
                    'quantity': int(char.progress.quantity),
                    'elapsed_time': char.progress.calculate_elapsed_time()})
        else:
            return jsonify({'error': 'Character not found.'})

