from flask import g, request, session
import logging

from .db_serializable import (
    Identifiable, MutableNamespace, coldef, tuple_to_pg_array)
from .item import Item
from .progress import Progress
from .utils import Pile, Storage, request_bool

tables_to_create = {
    'locations': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        masked boolean NOT NULL,
        progress_id integer,
        quantity float(4) NOT NULL,
        dimensions integer[2],
        excluded integer[4]
    """
}
logger = logging.getLogger(__name__)

class ItemAt(Pile):
    PILE_TYPE = Storage.LOCAL
    def __init__(self, item=None):
        super().__init__(item)
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

class Destination:
    def __init__(self, loc, distance):
        self.loc = loc
        self.distance = distance

class Grid:
    def __init__(self):
        self.dimensions = (0, 0)  # width, height
        self.excluded = (0, 0, 0, 0)  # left, top, right, bottom

class Location(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.masked = False
        self.destinations = {}  # Destination objects keyed by loc id
        self.items = []  # ItemAt objects
        self.pile = ItemAt()  # for Progress
        self.progress = Progress(container=self)
        self.grid = Grid()

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'masked': self.masked,
            'destinations': {
                str(dest.loc.id): dest.distance
                for dest in self.destinations.values()},
            'items': [
                item_at.to_json()
                for item_at in self.items],
            'progress': self.progress.to_json(),
            'quantity': self.pile.quantity,
            'dimensions': self.grid.dimensions,
            'excluded': self.grid.excluded
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.masked = data.get('masked', False)
        instance.destinations = {
            dest_id: Destination(Location(dest_id), distance)
            for dest_id, distance in data.get('destinations', {}).items()}
        instance.items = [
            ItemAt.from_json(item_data)
            for item_data in data.get('items', [])]
        instance.progress = Progress.from_json(
            data.get('progress', {}), instance)
        instance.pile.quantity = data.get('quantity', 0.0)
        instance.grid.dimensions = data.get('dimensions', (0, 0))
        instance.grid.excluded = data.get('excluded', (0, 0, 0, 0))
        return instance

    def json_to_db(self, doc):
        logger.debug("json_to_db()")
        self.progress.json_to_db(doc['progress'])
        doc['progress_id'] = self.progress.id
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
    def load_characters_at_loc(cls, id_to_get, load=True):
        from .character import Character
        query = """
            SELECT *
            FROM characters
            WHERE game_token = %s
        """
        values = [g.game_token]
        if id_to_get:
            query += " AND location_id = %s"
            values.append(id_to_get)
        query += "\nORDER BY name"
        chars = []
        characters_rows = cls.execute_select(query, values)
        for char_row in characters_rows:
            chars.append(Character.from_json(char_row))
        if load:
            g.game_data.set_list(Character, chars)
        return chars

    @classmethod
    def load_piles(cls, loc_id):
        logger.debug("load_piles(%d)", loc_id)
        query = """
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON {tables[1]}.loc_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
                AND {tables[0]}.id = %s
        """
        tables_rows = cls.select_tables(
            query, [g.game_token, loc_id], ['locations', 'loc_items'])
        loc = None
        for loc_data, item_data in tables_rows:
            if not loc:
                loc = cls.from_json(loc_data)
            if item_data.item_id:
                itemAt = ItemAt.from_json(item_data)
                itemAt.container = cls.get_by_id(loc_data.id)
                loc.items.append(itemAt)
                if not itemAt.container.items:
                    itemAt.container = loc
        if loc:
            pile = loc.items[0]
            logger.debug("item %s (%d) qty %.1f",
                pile.item.name, pile.item.id, pile.quantity)
        else:
            logger.debug("Returning default loc")
        return loc or Location()

    @classmethod
    def data_for_file(cls):
        logger.debug("data_for_file()")
        # Get loc and progress data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """, [g.game_token], ['locations', 'progress'])
        instances = {}  # keyed by ID
        for loc_data, progress_data in tables_rows:
            instance = instances.setdefault(
                loc_data.id, cls.from_json(loc_data))
            if progress_data.id:
                instance.progress = Progress.from_json(progress_data, instance)
        # Get destinations data
        dest_rows = cls.execute_select("""
            SELECT *
            FROM loc_destinations
            WHERE game_token = %s
        """, [g.game_token])
        for row in dest_rows:
            instance = instances[row.loc_id]
            instance.destinations[dest_data.dest_id] = dest_data.distance
        # Get loc item data
        item_rows = cls.execute_select("""
            SELECT *
            FROM loc_items
            WHERE game_token = %s
        """, [g.game_token])
        for row in item_rows:
            instance = instances[row.loc_id]
            instance.items.append(ItemAt.from_json(row))
        # Replace IDs with partial objects
        for instance in instances.values():
            dest_objs = {}
            for dest_id, distance in instance.destinations.items():
                dest_obj = Destination(Location(dest_id), distance)
                dest_objs[dest_id] = dest_obj
            instance.destinations = dest_objs
        # Print debugging info
        logger.debug("found %d locations", len(instances))
        for instance in instances.values():
            logger.debug("location %d (%s) has %d destinations",
                instance.id, instance.name, len(instance.destinations))
        return list(instances.values())

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        # Get all location data and the current loc's progress data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[0]}.id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (id_to_get, g.game_token), ['locations', 'progress'])
        g.game_data.locations = []
        current_data = MutableNamespace()
        for loc_data, progress_data in tables_rows:
            if loc_data.id == id_to_get:
                current_data = loc_data
                loc_data.progress = progress_data
            g.game_data.locations.append(Location.from_json(loc_data))
        # Get the current location's destination data
        loc_dest_rows = cls.execute_select("""
            SELECT *
            FROM loc_destinations
            WHERE game_token = %s
                AND loc_id = %s
        """, (g.game_token, id_to_get))
        dests_data = {}
        for row in loc_dest_rows:
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
        for dest_id, dest in current_obj.destinations.items():
            dest.loc = Location.get_by_id(dest_id)
        for item_at in current_obj.items:
            item_at.item = Item.get_by_id(item_at.item.id)
        return current_obj

    @classmethod
    def data_for_play(cls, id_to_get):
        logger.debug("data_for_play()")
        current_obj = cls.data_for_configure(id_to_get)
        cls.load_characters_at_loc(id_to_get)
        return current_obj

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            logger.debug("Saving changes.")
            logger.debug(request.form)
            entity_list = self.get_list()
            if self not in entity_list:
                entity_list.append(self)
            self.name = request.form.get('location_name')
            self.description = request.form.get('location_description')
            self.masked = request_bool(request, 'masked')
            self.grid.dimensions = tuple(
                map(int, request.form.get(
                    'dimensions').split('x')))
            self.grid.excluded = tuple(
                list(map(int, request.form.get(
                    'excluded_left_top').split(','))) +
                list(map(int, request.form.get(
                    'excluded_right_bottom').split(','))))
            destination_ids = request.form.getlist('destination_id[]')
            destination_distances = request.form.getlist('destination_distance[]')
            self.destinations = {}
            for dest_id, dest_dist in zip(
                    destination_ids, destination_distances):
                self.destinations[dest_id] = Destination(
                    Location(dest_id), int(dest_dist))
            item_ids = request.form.getlist('item_id[]')
            item_qtys = request.form.getlist('item_qty[]')
            item_posits = request.form.getlist('item_pos[]')
            self.items = []
            for item_id, item_qty, item_pos in zip(
                    item_ids, item_qtys, item_posits):
                item = Item(int(item_id))
                item_at = ItemAt(item)
                item_at.quantity = int(item_qty)
                item_at.position = tuple(
                    map(int, item_pos.split(',')))
                self.items.append(item_at)
            self.to_db()
        elif 'delete_location' in request.form:
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed location.'
            except DbError as e:
                raise DeletionError(e)
        elif 'cancel_changes' in request.form:
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")
