from flask import g, request, session
from .db_serializable import (
    Identifiable, MutableNamespace, coldef, tuple_to_pg_array)
from .item import Item

tables_to_create = {
    'locations': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        dimensions integer[2]
    """
}

class ItemAt:
    def __init__(self, item=None):
        self.item = item
        if not item:
            self.item = Item()
        self.quantity = 0
        self.position = (0, 0)

    def to_json(self):
        return {
            'item_id': self.item.id,
            'quantity': self.quantity,
            'position': self.position,
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        item_id = int(data.get('item_id', 0))
        instance = cls(Item(item_id))
        instance.quantity = data.get('quantity', 0)
        instance.position = tuple(data.get('position', (0, 0)))
        return instance

class Location(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.destinations = {}  # Location objects and their distance
        self.items = []  # ItemAt objects

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'destinations': {
                str(dest.id): distance
                for dest, distance in self.destinations.items()},
            'items': [
                item_at.to_json()
                for item_at in self.items],
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.destinations = {
            Location(int(dest_id)): distance
            for dest_id, distance in data.get('destinations', {}).items()}
        instance.items = [
            ItemAt.from_json(item_data)
            for item_data in data.get('items', [])]
        return instance

    def json_to_db(self, doc):
        print(f"{self.__class__.__name__}.json_to_db()")
        super().json_to_db(doc)
        for rel_table in ('loc_destinations', 'loc_items'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE loc_id = %s AND game_token = %s
            """, (self.id, self.game_token))
        if doc['destinations']:
            values = [
                (g.game_token, self.id, dest_id, distance)
                for dest_id, distance in doc['destinations'].items()]
            self.insert_multiple(
                "loc_destinations",
                "game_token, loc_id, dest_id, distance",
                values)
        if doc['items']:
            values = []
            for item_data in doc['items']:
                values.append((
                    g.game_token, self.id,
                    item_data['item_id'],
                    item_data['quantity'],
                    tuple_to_pg_array(item_data['position'])
                ))
            self.insert_multiple(
                "loc_items",
                "game_token, loc_id, item_id, quantity, position",
                values)

    @classmethod
    def data_for_file(cls):
        print(f"{cls.__name__}.data_for_file()")
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.loc_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            LEFT JOIN {tables[2]}
                ON {tables[2]}.loc_id = {tables[0]}.id
                AND {tables[2]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """
        values = [g.game_token]
        tables_rows = cls.select_tables(
            query, values,
            ['locations', 'loc_destinations', 'loc_items'])
        instances = {}  # keyed by ID
        for loc_data, dest_data, loc_item_data in tables_rows:
            print(f"loc_data {loc_data}")
            print(f"dest_data {dest_data}")
            print(f"loc_item_data {loc_item_data}")
            instance = instances.get(loc_data.id)
            if not instance:
                instance = cls.from_json(vars(loc_data))
                instances[loc_data.id] = instance
            if dest_data.dest_id:
                instance.destinations[dest_data.dest_id] = dest_data.distance
            if loc_item_data.item_id:
                instance.items.append(ItemAt.from_json(loc_item_data))
        # Replace IDs with partial objects
        for instance in instances.values():
            loc_objs = {}
            for dest_id, distance in instance.destinations.items():
                loc_obj = Location(dest_id)
                loc_objs[loc_obj] = distance
            instance.destinations = loc_objs
        # Print debugging info
        print(f"found {len(instances)} locations")
        for instance in instances.values():
            print(f"location {instance.id} ({instance.name})"
                " has {len(instance.destinations)} destinations")
        return list(instances.values())

    @classmethod
    def data_for_configure(cls, id_to_get):
        print(f"{cls.__name__}.data_for_configure()")
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        # Get all location data
        locations_data = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
            ORDER BY {table}.name
        """, (g.game_token,))
        g.game_data.locations = []
        current_data = MutableNamespace()
        for loc_data in locations_data:
            if loc_data.id == id_to_get:
                current_data = loc_data
            g.game_data.locations.append(Location.from_json(loc_data))
        # Get the current location's destination data
        loc_dest_data = cls.execute_select("""
            SELECT *
            FROM loc_destinations
            WHERE game_token = %s
                AND loc_id = %s
        """, (g.game_token, id_to_get))
        dests_data = {}
        for row in loc_dest_data:
            dests_data[row.dest_id] = row.distance
        current_data.destinations = dests_data
        # Get all item data and the current location's item relation data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[0]}.id = {tables[1]}.item_id
                AND {tables[0]}.game_token = {tables[1]}.game_token
                AND {tables[1]}.loc_id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (id_to_get, g.game_token), ['items', 'loc_items'])
        for item_data, loc_item_data in tables_rows:
            if loc_item_data.loc_id:
                current_data.setdefault('items', []).append(
                    vars(loc_item_data))
            g.game_data.items.append(Item.from_json(item_data))
        # Create item from data
        current_obj = Location.from_json(current_data)
        # Replace partial objects with fully populated objects
        populated_objs = {}
        for partial_item, distance in current_obj.destinations.items():
            loc = Location.get_by_id(partial_item.id)
            populated_objs[loc] = distance
        current_obj.destinations = populated_objs
        for item_at in current_obj.items:
            item_at.item = Item.get_by_id(item_at.item.id)
        return current_obj

    @classmethod
    def data_for_play(cls, id_to_get):
        print(f"{cls.__name__}.data_for_play()")
        from .character import Character
        current_obj = cls.data_for_configure(id_to_get)
        characters_data = cls.execute_select("""
            SELECT *
            FROM characters
            WHERE game_token = %s
                AND location_id = %s
            ORDER BY name
        """, (g.game_token, id_to_get))
        g.game_data.characters = []
        for char_data in characters_data:
            g.game_data.characters.append(Character.from_json(char_data))
        return current_obj

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            entity_list = self.get_list()
            if self not in entity_list:
                entity_list.append(self)
            self.name = request.form.get('location_name')
            self.description = request.form.get('location_description')
            destination_ids = request.form.getlist('destination_id[]')
            destination_distances = request.form.getlist('destination_distance[]')
            self.destinations = {}
            for dest_id, dest_dist in zip(
                    destination_ids, destination_distances):
                dest_location = Location(int(dest_id))
                self.destinations[dest_location] = int(dest_dist)
            item_ids = request.form.getlist('item_id[]')
            item_qtys = request.form.getlist('item_qty[]')
            self.items = []
            for item_id, item_qty in zip(item_ids, item_qtys):
                item = Item(int(item_id))
                item_at = ItemAt(item)
                item_at.quantity = int(item_qty)
                self.items.append(item_at)
            self.to_db()
        elif 'delete_location' in request.form:
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed location.'
            except DbError as e:
                raise DeletionError(e)
        elif 'cancel_changes' in request.form:
            print("Cancelling changes.")
        else:
            print("Neither button was clicked.")

    def distance(self, other_location):
        return self.destinations.get(other_location, -1)
