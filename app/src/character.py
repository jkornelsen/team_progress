from flask import g, request, session

from .attrib import Attrib
from .db_serializable import Identifiable, MutableNamespace, coldef
from .item import Item
from .location import Location
from .progress import Progress
from .utils import Storage, request_bool, request_float

tables_to_create = {
    'characters': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        progress_id integer,
        quantity float(4) NOT NULL,
        location_id integer,
        dest_id integer,
        position integer[2],
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """,
}

class OwnedItem:
    PILE_TYPE = Storage.CARRIED
    def __init__(self, item=None):
        self.item = item
        if not item:
            self.item = Item()
        self.container = None  # character who owns item
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

    def equipped(self):
        """If not equipped (worn or held), then it's assumed to be carried in
        inventory, such as a backpack."""
        return bool(self.slot)

class Character(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.get_list()) > 1 else True
        self.attribs = {}  # keys are Attrib object, values are stat val
        self.items = []  # OwnedItem objects
        self.pile = OwnedItem()  # for Progress
        self.progress = Progress(container=self)  # for travel or item recipes
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
            'quantity': self.pile.quantity,
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
        instance.pile.quantity = data.get('quantity', 0.0)
        instance.destination = Location.get_by_id(
            int(data['dest_id'])) if data.get('dest_id', 0) else None
        return instance

    def json_to_db(self, doc):
        print(f"{self.__class__.__name__}.json_to_db()")
        self.progress.json_to_db(doc['progress'])
        doc['progress_id'] = self.progress.id
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
    def data_for_file(cls):
        print(f"{cls.__name__}.data_for_file()")
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
                instance.attribs[Attrib(attrib_data.attrib_id)] = attrib_data.value
            if char_item_data.item_id:
                instance.items.append(
                    OwnedItem.from_json(char_item_data))
        # Print debugging info
        print(f"found {len(instances)} characters")
        for instance in instances.values():
            print(f"character {instance.id} ({instance.name})"
                " has {len(instance.attribs)} attribs")
        return list(instances.values())

    @classmethod
    def data_for_configure(cls, id_to_get):
        print(f"{cls.__name__}.data_for_configure()")
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
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
        """, (id_to_get, g.game_token), ['characters', 'progress'])
        g.game_data.characters = []
        current_data = MutableNamespace()
        for char_data, progress_data in tables_rows:
            if char_data.id == id_to_get:
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
        """, (id_to_get, g.game_token), ['attribs', 'char_attribs'])
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
        """, (id_to_get, g.game_token), ['items', 'char_items'])
        for item_data, char_item_data in tables_rows:
            if char_item_data.char_id:
                current_data.setdefault(
                    'items', []).append(char_item_data)
            g.game_data.items.append(Item.from_json(item_data))
        # Get all location names
        from .game_data import GameData
        GameData.entity_names_from_db([Location])
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

    @classmethod
    def data_for_play(cls, id_to_get):
        print(f"{cls.__name__}.data_for_play()")
        current_obj = cls.data_for_configure(id_to_get)
        if current_obj.location:
            # Get the current location's destination data
            loc_dest_data = cls.execute_select("""
                SELECT *
                FROM loc_destinations
                WHERE game_token = %s
                    AND loc_id = %s
            """, (g.game_token, current_obj.location.id))
            dests_data = {}
            for row in loc_dest_data:
                dests_data[row.dest_id] = row.distance
            destinations = dests_data
            # Replace IDs with objects
            loc_objs = {}
            for dest_id, distance in destinations.items():
                loc = Location.get_by_id(dest_id)
                loc_objs[loc] = distance
            current_obj.location.destinations = loc_objs
            # Travel distance
            distance = current_obj.location.destinations.get(
                current_obj.destination, 0)
            current_obj.pile.item.q_limit = distance
        return current_obj

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            self.name = request.form.get('char_name')
            self.description = request.form.get('char_description')
            self.toplevel = request_bool(request, 'top_level')
            #if self.progress.is_ongoing:
            #    self.progress.stop()
            item_ids = request.form.getlist('item_id[]')
            item_qtys = request.form.getlist('item_qty[]')
            item_slots = request.form.getlist('item_slot[]')
            self.items = []
            for item_id, item_qty, item_slot in zip(
                    item_ids, item_qtys, item_slots):
                ownedItem = OwnedItem(Item(int(item_id)))
                self.items.append(ownedItem)
                ownedItem.quantity = float(item_qty)
                ownedItem.slot = item_slot
            location_id = request.form.get('char_location')
            self.location = Location.get_by_id(
                int(location_id)) if location_id else None
            attrib_ids = request.form.getlist('attrib_id')
            print(f"Attrib IDs: {attrib_ids}")
            self.attribs = {}
            for attrib_id in attrib_ids:
                attrib_val = request_float(
                    request, f'attrib_{attrib_id}_val', 0.0)
                attrib_item = Attrib.get_by_id(attrib_id)
                self.attribs[attrib_item] = attrib_val
            print("attribs: ", {attrib.name: val
                for attrib, val in self.attribs.items()})
            self.to_db()
        elif 'delete_character' in request.form:
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed character.'
            except DbError as e:
                raise DeletionError(e)
        elif 'cancel_changes' in request.form:
            print("Cancelling changes.")
        else:
            print("Neither button was clicked.")
