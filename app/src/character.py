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
from .db_serializable import (
    Identifiable, MutableNamespace, coldef, new_game_data)
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
        progress_id integer,
        location_id integer,
        dest_id integer,
        position integer[2],
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """,
}

class OwnedItem:
    def __init__(self, item=None):
        self.item = item
        if not item:
            self.item = Item()
        self.quantity = 0
        self.slot = ''  # for example, "main hand"

    def to_json(self):
        return {
            'item_id': self.item.id,
            'quantity': self.quantity,
            'slot': self.slot,
        }

    @classmethod
    def from_json(cls, data):
        instance = cls(Item(int(data.get('item_id', 0))))
        instance.quantity = data.get('quantity', 0)
        instance.slot = data.get('slot', "")
        return instance

    def worn(self):
        """If not worn, then it's assumed to be carried in inventory,
        such as a backpack."""
        return bool(self.slot)

class Character(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.get_list()) > 1 else True
        self.attribs = {}  # keys are Attrib object, values are stat val
        self.items = []  # OwnedItem objects
        self.progress = Progress(entity=self)  # for travel or perhaps other actions
        self.location = None  # Location object
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
            'items': [
                owned.to_json() for owned in self.items],
            'location_id': self.location.id if self.location else None,
            'progress': self.progress.to_json(),
            'dest_id': self.destination.id if self.destination else None
        }

    @classmethod
    def from_json(cls, data, _=None):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', '')
        instance.toplevel = data.get('toplevel', False)
        instance.items = [
            OwnedItem.from_json(owned_data)
            for owned_data in data.get('items', [])]
        instance.attribs = {
            Attrib.get_by_id(int(attrib_id)): val
            for attrib_id, val in data.get('attribs', {}).items()}
        instance.location = Location.get_by_id(
            int(data['location_id'])) if data.get('location_id', 0) else None
        instance.progress = Progress.from_json(
            data.get('progress', {}), instance)
        instance.destination = Location.get_by_id(
            int(data['dest_id'])) if data.get('dest_id', 0) else None
        return instance

    def json_to_db(self, doc):
        print(f"{self.__class__.__name__}.json_to_db()")
        super().json_to_db(doc)
        for rel_table in ('char_attribs', 'char_items'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE char_id = %s AND game_token = %s
            """, (self.id, self.game_token))
        if doc['attribs']:
            values = [
                (g.game_token, self.id, attrib_id, val)
                for attrib_id, val in doc['attribs'].items()]
            self.insert_multiple(
                "char_attribs",
                "game_token, char_id, attrib_id, value",
                values)
        if doc['items']:
            print(f"items: {doc['items']}")
            values = []
            for owned_item in doc['items']:
                values.append((
                    g.game_token, self.id,
                    owned_item['item_id'],
                    owned_item['quantity'],
                    owned_item['slot']
                    ))
            self.insert_multiple(
                "char_items",
                "game_token, char_id, item_id, quantity, slot",
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
                ON {tables[2]}.char_id = {tables[0]}.id
                AND {tables[2]}.game_token = {tables[0]}.game_token
            LEFT JOIN {tables[3]}
                ON {tables[3]}.char_id = {tables[0]}.id
                AND {tables[3]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """
        values = [g.game_token]
        if id_to_get:
            query = f"{query}\nAND {{tables[0]}}.id = %s"
            values.append(id_to_get);
        tables_rows = cls.select_tables(
            query, values,
            ['characters', 'progress', 'char_attribs', 'char_items'])
        instances = {}  # keyed by ID
        for char_data, progress_data, attrib_data, char_item_data in tables_rows:
            print(f"char_data {char_data}")
            print(f"progress_data {progress_data}")
            print(f"attrib_data {attrib_data}")
            print(f"char_item_data {char_item_data}")
            instance = instances.get(char_data.id)
            if not instance:
                instance = cls.from_json(vars(char_data))
                instances[char_data.id] = instance
            if progress_data.id:
                instance.progress = Progress.from_json(progress_data, instance)
            if attrib_data.attrib_id:
                instance.attribs[attrib_data.attrib_id] = attrib_data.value
            if char_item_data.item_id:
                instance.items.append(
                    OwnedItem.from_json(char_item_data))
        # Replace IDs with partial objects
        for instance in instances.values():
            attrib_objs = {}
            for attrib_id, attrib_val in instance.attribs.items():
                attrib_obj = Attrib(attrib_id)
                attrib_objs[attrib_obj] = attrib_val
            instance.attribs = attrib_objs
        # Print debugging info
        print(f"found {len(instances)} characters")
        for instance in instances.values():
            print(f"character {instance.id} ({instance.name})"
                " has {len(instance.attribs)} attribs")
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
        # Get all character data and the current character's progress data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[0]}.id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (config_id, g.game_token), ['characters', 'progress'])
        g.game_data.characters = []
        current_data = MutableNamespace()
        for char_data, progress_data in tables_rows:
            if char_data.id == config_id:
                current_data = char_data
                if progress_data.id:
                    char_data.progress = progress_data
            g.game_data.characters.append(Character.from_json(char_data))
        # Get all attrib data and the current character's attrib relation data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.attrib_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.char_id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (config_id, g.game_token), ['attribs', 'char_attribs'])
        for attrib_data, char_attrib_data in tables_rows:
            if char_attrib_data.attrib_id:
                current_data.setdefault(
                    'attribs', {})[attrib_data.id] = char_attrib_data.value
            g.game_data.attribs.append(Attrib.from_json(attrib_data))
        # Get all item data and the current character's item relation data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[0]}.id = {tables[1]}.item_id
                AND {tables[0]}.game_token = {tables[1]}.game_token
                AND {tables[1]}.char_id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (config_id, g.game_token), ['items', 'char_items'])
        for item_data, char_item_data in tables_rows:
            if char_item_data.char_id:
                current_data.setdefault(
                    'items', []).append(char_item_data)
            g.game_data.items.append(Item.from_json(item_data))
        # Get all location names
        locations_data = cls.execute_select("""
            SELECT id, name
            FROM locations
            WHERE game_token = %s
            ORDER BY name
        """, (g.game_token,))
        g.game_data.locations = [
            Location.from_json(loc_data)
            for loc_data in locations_data]
        # Create character from data
        current_obj = Character.from_json(current_data)
        # Replace partial objects with fully populated objects
        populated_objs = {}
        for partial_attrib, val in current_obj.attribs.items():
            attrib = Attrib.get_by_id(partial_attrib.id)
            populated_objs[attrib] = val
        current_obj.attribs = populated_objs
        for owned_item in current_obj.items:
            owned_item.item = Item.get_by_id(owned_item.item.id)
        # Print debugging info
        print(f"found {len(current_obj.items)} owned items")
        if len(current_obj.items):
            owned_item = current_obj.items[0]
            print(f"item_id={owned_item.item.id}")
            print(f"name={owned_item.item.name}")
            print(f"quantity={owned_item.quantity}")
            print(f"slot={owned_item.slot}")
        return current_obj

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            self.name = request.form.get('char_name')
            self.description = request.form.get('char_description')
            self.toplevel = bool(request.form.get('top_level'))
            if self.progress.is_ongoing:
                self.progress.stop()
            item_ids = request.form.getlist('item_id[]')
            item_qtys = request.form.getlist('item_qty[]')
            item_slots = request.form.getlist('item_slot[]')
            self.items = []
            for item_id, item_qty, item_slot in zip(
                    item_ids, item_qtys, item_slots):
                ownedItem = OwnedItem(Item(int(item_id)))
                self.items.append(ownedItem)
                ownedItem.quantity = int(item_qty)
                ownedItem.slot = item_slot
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
    @app.route('/configure/character/<char_id>', methods=['GET', 'POST'])
    def configure_char(char_id):
        new_game_data()
        instance = Character.data_for_configure(char_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/character.html',
                current=instance,
                game_data=g.game_data)
        else:
            return instance.configure_by_form()

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

