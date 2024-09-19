from flask import g, session
import logging

from .db_serializable import (
    Identifiable, MutableNamespace, coldef, tuple_to_pg_array)
from .item import Item
from .progress import Progress
from .utils import Pile, RequestHelper, Storage

tables_to_create = {
    'locations': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        toplevel boolean NOT NULL,
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
        self.default_pos = None  # legal position in grid if any

    def set_default_pos(self):
        """Returns None if there are no legal positions.
        Call this method whenever changing dimensions or excluded.
        """
        width, height = self.dimensions
        left, top, right, bottom = self.excluded
        for y in range(height):
            for x in range(width):
                if not (left <= x <= right and top <= y <= bottom):
                    self.default_pos = x, y
                    return
        self.default_pos = None

    def in_grid(self, pos):
        """Returns True if position is legally in the grid."""
        if not pos:
            return False
        x, y = pos
        width, height = self.dimensions
        left, top, right, bottom = self.excluded
        if x < 0 or x > width - 1:
            return False
        if y < 0 or y > height - 1:
            return False
        if x >= left and x <= right and y >= top and y <= bottom:
            return False
        return True
        

class Location(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = True
        self.masked = False
        self.destinations = {}  # Destination objects keyed by loc id
        self.items = {}  # ItemAt objects keyed by item id
        # producing local items? Maybe char would do that.
        self.progress = Progress(container=self)
        self.pile = Pile()  # for Progress
        self.grid = Grid()

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'masked': self.masked,
            'destinations': {
                str(dest.loc.id): dest.distance
                for dest in self.destinations.values()},
            'items': [
                item_at.to_json()
                for item_at in self.items.values()],
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
        instance.toplevel = data.get('toplevel', False)
        instance.masked = data.get('masked', False)
        instance.destinations = {
            dest_id: Destination(Location(dest_id), distance)
            for dest_id, distance in data.get('destinations', {}).items()}
        for item_data in data.get('items', []):
            if not isinstance(item_data, dict):
                item_data = vars(item_data)
            instance.items[
                item_data.get('item_id', 0)] = ItemAt.from_json(item_data)
        instance.pile = Pile()
        instance.pile.quantity = data.get('quantity', 0.0)
        instance.progress = Progress.from_json(
            data.get('progress', {}), instance)
        instance.grid.dimensions = data.get('dimensions') or (0, 0)
        instance.grid.excluded = data.get('excluded') or (0, 0, 0, 0)
        instance.grid.set_default_pos()
        return instance

    def json_to_db(self, doc):
        logger.debug("json_to_db()")
        self.progress.json_to_db(doc['progress'])
        doc['progress_id'] = None if self.progress.id == 0 else self.progress.id
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
    def load_complete_object(cls, id_to_get):
        """Load an object with everything needed for storing to db."""
        logger.debug("load_complete_object(%s)", id_to_get)
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        # Get this location's base data and progress data
        tables_row = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
                AND {tables[0]}.id = %s
            """, (g.game_token, id_to_get), ['locations', 'progress'],
            fetch_all=False)
        current_data = MutableNamespace()
        if tables_row:
            loc_data, progress_data = tables_row
            current_data = loc_data
            loc_data.progress = progress_data
        # Get this location's destination data
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
        # Get this location's item relation data
        loc_items_rows = cls.execute_select("""
            SELECT *
            FROM loc_items
            WHERE game_token = %s
                AND loc_id = %s
            """, (g.game_token, id_to_get))
        for item_data in loc_items_rows:
            current_data.setdefault('items', []).append(vars(item_data))
        # Create object from data
        return Location.from_json(current_data)

    @classmethod
    def load_complete_objects(cls):
        """Load objects with everything needed for storing to JSON file."""
        logger.debug("load_complete_objects()")
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
            instance.destinations[row.dest_id] = row.distance
        # Get loc item data
        item_rows = cls.execute_select("""
            SELECT *
            FROM loc_items
            WHERE game_token = %s
            """, [g.game_token])
        for row in item_rows:
            instance = instances[row.loc_id]
            instance.items[row.item_id] = ItemAt.from_json(row)
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
        # Set list of objects
        g.game_data.set_list(cls, list(instances.values()))
        return g.game_data.get_list(cls)

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_object(id_to_get)
        # Get all basic location and item data
        g.game_data.from_db_flat([Location, Item])
        # Replace partial objects with fully populated objects
        for dest_id, dest in current_obj.destinations.items():
            dest.loc = Location.get_by_id(dest_id)
        for item_at in current_obj.items.values():
            item_at.item = Item.get_by_id(item_at.item.id)
            if not current_obj.grid.in_grid(item_at.position):
                item_at.position = current_obj.grid.default_pos
        return current_obj

    @classmethod
    def data_for_play(cls, id_to_get):
        logger.debug("data_for_play(%s)", id_to_get)
        current_obj = cls.data_for_configure(id_to_get)
        from .character import Character
        chars = Character.load_characters_at_loc(id_to_get)
        for char in chars:
            if char.location.id == current_obj.id:
                if not current_obj.grid.in_grid(char.position):
                    char.position = current_obj.grid.default_pos
        return current_obj

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes') or req.has_key('make_duplicate'):
            req.debug()
            entity_list = self.get_list()
            if self not in entity_list:
                entity_list.append(self)
            self.name = req.get_str('location_name')
            self.description = req.get_str('location_description')
            self.toplevel = req.get_bool('top_level')
            req = RequestHelper('form')
            self.masked = req.get_bool('masked')
            self.grid.dimensions = tuple(
                map(int, req.get_str(
                    'dimensions').split('x')))
            self.grid.excluded = tuple(
                list(map(int, req.get_str(
                    'excluded_left_top').split(','))) +
                list(map(int, req.get_str(
                    'excluded_right_bottom').split(','))))
            self.grid.set_default_pos()
            destination_ids = req.get_list('destination_id[]')
            destination_distances = req.get_list('destination_distance[]')
            self.destinations = {}
            for dest_id, dest_dist in zip(
                    destination_ids, destination_distances):
                self.destinations[dest_id] = Destination(
                    Location(dest_id), int(dest_dist))
            item_ids = req.get_list('item_id[]')
            item_qtys = req.get_list('item_qty[]')
            item_posits = req.get_list('item_pos[]')
            self.items = {}
            old = Location.load_complete_object(self.id)
            for item_id, item_qty, item_pos in zip(
                    item_ids, item_qtys, item_posits):
                item_at = ItemAt(Item(int(item_id)))
                try:
                    item_at.position = tuple(
                        map(int, item_pos.split(',')))
                except ValueError:
                    pass  # use default value
                self.items[item_id] = item_at
                old_item = old.items.get(item_id, None)
                old_qty = old_item.quantity if old_item else 0
                item_at.quantity = req.set_num_if_changed(item_qty, old_qty)
            self.to_db()
        elif req.has_key('delete_location'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed location.'
            except DbError as e:
                raise DeletionError(e)
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")
