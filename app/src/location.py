import logging
from typing import Tuple, Union

from flask import g, session

from .db_serializable import (
    DbError, DeletionError, Identifiable, QueryHelper, Serializable, coldef)
from .item import Item
from .pile import Pile
from .progress import Progress
from .utils import NumTup, RequestHelper

logger = logging.getLogger(__name__)
tables_to_create = {
    'locations': f"""
        {coldef('name')},
        toplevel boolean NOT NULL,
        masked boolean NOT NULL,
        progress_id integer,
        quantity float(4) NOT NULL,
        dimensions integer[2],
        excluded integer[4],
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
            DEFERRABLE INITIALLY DEFERRED
        """
    }

class ItemAt(Pile):
    def __init__(self, item, loc):
        super().__init__(item=item, container=loc)
        self.position = NumTup((0, 0))

    @classmethod
    def container_type(cls):
        return Location.typename()

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'item_id': self.item.id,
            'quantity': self.quantity,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'position': self.position.as_list(),
            })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'position': self.position,
            })
        return data

    @classmethod
    def from_data(cls, data, loc=None):  #pylint: disable=arguments-differ
        data = cls.prepare_dict(data)
        instance = cls(None, loc)
        instance.set_basic_data(data)
        instance.item = Item(instance.item_id)
        instance.position = NumTup(data.get('position', (0, 0)))
        return instance

class Destination(Serializable):
    def __init__(
            self,
            loc1: 'Location',
            loc2: 'Location',
            current_loc_id: int = 0):
        super().__init__()
        self.loc1 = loc1
        self.loc2 = loc2
        self.distance = 1
        self.door1 = (0, 0)  # at loc 1
        self.door2 = (0, 0)  # at loc 2
        self.bidirectional = True
        self.current_loc_id = current_loc_id  # which loc is "here"

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'distance': self.distance,
            'bidirectional': self.bidirectional,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'loc2_id': self.loc2.id,
            'door1': self.door1.as_list(),
            'door2': self.door2.as_list(),
            })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'loc1_id': self.loc1.id,
            'loc2_id': self.loc2.id,
            'door1': self.door1,
            'door2': self.door2,
            })
        return data

    @classmethod
    def from_data(
            cls,
            data: Union[dict, 'MutableNamespace'],
            current_loc_id: int = 0):
        data = cls.prepare_dict(data)
        instance = cls(
            Location(int(data.get('loc1_id', 0))),
            Location(int(data.get('loc2_id', 0))),
            current_loc_id)
        instance.distance = data.get('distance', 0)
        instance.door1 = NumTup(data.get('door1', (0, 0)))
        instance.door2 = NumTup(data.get('door2', (0, 0)))
        instance.bidirectional = data.get('bidirectional', True)
        return instance

    @property
    def is_main(self):
        """The current location is the main one for this dest pair,
        so it's loc1, and json data is stored under this loc.
        """
        return self.current_loc_id and self.loc1.id == self.current_loc_id

    def _other_index(self, other: bool):
        """Get the one for 'there' if other is true.
        Unless otherwise specified, loc1 is 'here'.
        """
        if self.is_main:
            return 1 if other else 0
        return 0 if other else 1

    def _get_loc(self, other: bool) -> 'Location':
        locs = [self.loc1, self.loc2]
        return locs[self._other_index(other)]

    def _get_door(self, other: bool) -> Tuple[int, int]:
        doors = [self.door1, self.door2]
        return doors[self._other_index(other)]

    @property
    def other_loc(self) -> 'Location':
        return self._get_loc(True)

    @property
    def loc_here(self) -> 'Location':
        return self._get_loc(False)

    @property
    def other_door(self) -> Tuple[int, int]:
        return self._get_door(True)

    @property
    def door_here(self) -> Tuple[int, int]:
        return self._get_door(False)

class Location(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = False
        self.masked = False
        self.destinations = []  # Destination objects
        self.items = {}  # ItemAt objects keyed by item id
        # producing local items? Maybe char would do that.
        self.progress = Progress(container=self)
        self.pile = Pile()  # for Progress
        self.grid = Grid()

    @classmethod
    def typename(cls):
        return 'loc'

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'masked': self.masked,
            'quantity': self.pile.quantity,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'destinations': [
                dest.dict_for_json()
                for dest in self.destinations
                if dest.loc1.id == self.id],
            'items': [
                item_at.dict_for_json()
                for item_at in self.items.values()],
            'progress': self.progress.dict_for_json(),
            'dimensions': self.grid.dimensions.as_list(),
            'excluded': self.grid.excluded.as_list()
            })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'progress_id': self.progress.id or None,
            'dimensions': self.grid.dimensions,
            'excluded': self.grid.excluded
            })
        return data

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = super().from_data(data)
        instance.toplevel = data.get('toplevel', False)
        instance.masked = data.get('masked', False)
        for dest_data in data.get('destinations', []):
            loc1_id = dest_data.get('loc1_id', 0)
            loc2_id = dest_data.get('loc2_id', 0)
            if instance.id not in (loc1_id, loc2_id):
                logger.debug(
                    "neither loc_id is %s; expected when loading from json",
                    instance.id)
                if loc2_id and not loc1_id:
                    dest_data['loc1_id'] = instance.id
                elif loc1_id and not loc2_id:
                    dest_data['loc2_id'] = instance.id
                else:
                    raise ValueError(
                        f"Incorrect destination data for loc {instance.id}")
            instance.destinations.append(
                Destination.from_data(dest_data, instance.id))
        for item_data in data.get('items', []):
            if not isinstance(item_data, dict):
                item_data = vars(item_data)
            item_at = ItemAt.from_data(item_data, instance)
            instance.items[item_data.get('item_id', 0)] = item_at
        instance.pile = Pile()
        instance.pile.quantity = data.get('quantity', 0.0)
        instance.progress = Progress.from_data(
            data.get('progress', {}), instance)
        instance.grid.dimensions = NumTup(data.get('dimensions', (0, 0)))
        instance.grid.excluded = NumTup(data.get('excluded', (0, 0, 0, 0)))
        instance.grid.set_default_pos()
        return instance

    def to_db(self):
        logger.debug("to_db()")
        self.progress.to_db()
        super().to_db()
        self.execute_change("""
            DELETE FROM loc_destinations
            WHERE game_token = %s AND loc1_id = %s
            """, (g.game_token, self.id))
        self.execute_change("""
            DELETE FROM loc_items
            WHERE game_token = %s AND loc_id = %s
            """, (g.game_token, self.id))
        if self.destinations:
            values_to_insert = []
            for dest in self.destinations:
                values_to_insert.append((
                    g.game_token,
                    dest.loc1.id,
                    dest.loc2.id,
                    dest.distance,
                    dest.door1.as_pg_array(),
                    dest.door2.as_pg_array(),
                    dest.bidirectional,
                    ))
                if dest.loc2.id == self.id:
                    self.execute_change("""
                        DELETE FROM loc_destinations
                        WHERE game_token = %s
                            AND loc1_id = %s AND loc2_id = %s
                        """, (g.game_token, dest.loc1.id, dest.loc2.id))
            self.insert_multiple(
                "loc_destinations",
                "game_token, loc1_id, loc2_id, distance,"
                " door1, door2, bidirectional",
                values_to_insert)
        if self.items:
            values_to_insert = []
            for item_id, item_at in self.items.items():
                values_to_insert.append((
                    g.game_token, self.id,
                    item_id,
                    item_at.quantity,
                    item_at.position.as_pg_array(),
                    ))
            self.insert_multiple(
                "loc_items",
                "game_token, loc_id, item_id, quantity, position",
                values_to_insert)

    def unmask(self):
        """Mark this location as visited if it hasn't been yet."""
        if self.id:
            self.execute_change("""
                UPDATE locations
                SET masked = false
                WHERE id = %s AND masked = true
                """, (self.id,))

    @classmethod
    def get_destinations_from(cls, departure_id, current_dest_id):
        """Get all the destinations you can travel to from the given
        departure_id.
        Also, returns the destination matching current_dest_id, if any.
        """
        # Retrieve the destination loc (not the departure loc)
        # for paths between locations when the current location
        # is involved in the connection
        # and the path can be travelled from this direction. 
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON ({tables[1]}.id = {tables[0]}.loc2_id
                    OR ({tables[1]}.id = {tables[0]}.loc1_id
                        AND {tables[0]}.bidirectional = TRUE))
                AND {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.id != %s
            WHERE {tables[0]}.game_token = %s
                AND ({tables[0]}.loc1_id = %s
                    OR ({tables[0]}.loc2_id = %s
                    AND {tables[0]}.bidirectional = TRUE))
            """, [departure_id, g.game_token, departure_id, departure_id],
            ['loc_destinations', 'locations'])
        destinations = []
        for dest_data, dest_loc_data in tables_rows:
            dest = Destination.from_data(dest_data, departure_id)
            dest_loc = Location.from_data(dest_loc_data)
            if dest.is_main:
                dest.loc2 = dest_loc
            else:
                dest.loc1 = dest_loc
            destinations.append(dest)
        current_dest = None
        if current_dest_id:
            for dest in destinations:
                if ((dest.is_main and dest.loc2.id == current_dest_id)
                        or (dest.loc2.id == departure_id
                            and dest.loc1.id == current_dest_id
                            and dest.bidirectional)):
                    current_dest = dest
                    break
        logger.debug("Destinations: %s", len(destinations))
        return destinations, current_dest

    @classmethod
    def load_complete_objects(cls, id_to_get=None):
        """Load objects with everything needed for storing to db
        or JSON file.
        :param id_to_get: specify to only load a single object
        """
        logger.debug("load_complete_objects(%s)", id_to_get)
        if id_to_get in ['new', '0', 0]:
            return cls()
        locs = Progress.load_base_data(cls, id_to_get)
        # Get destination data
        qhelper = QueryHelper("""
            SELECT *
            FROM loc_destinations
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit_expr(
            "(loc1_id = %s OR loc2_id = %s)",
            [id_to_get, id_to_get])
        dest_rows = cls.execute_select(qhelper=qhelper)
        for row in dest_rows:
            loc_id = row.loc1_id
            if id_to_get and row.loc2_id == int(id_to_get):
                loc_id = row.loc2_id
            loc = locs[loc_id]
            loc.setdefault('destinations', []).append(row)
        # Get this location's item relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM loc_items
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("loc_id", id_to_get)
        item_rows = cls.execute_select(qhelper=qhelper)
        for row in item_rows:
            loc = locs[row.loc_id]
            loc.setdefault('items', []).append(row)
        # Set list of objects
        instances = []
        for data in locs.values():
            instances.append(cls.from_data(data))
        if id_to_get:
            return instances[0]
        g.game_data.set_list(cls, instances)
        return instances

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_objects(id_to_get)
        # Replace partial objects with fully populated objects
        g.game_data.from_db_flat([Location, Item])
        for dest in current_obj.destinations:
            if dest.loc1.id == int(id_to_get):
                dest.loc1 = current_obj
                dest.loc2 = Location.get_by_id(dest.loc2.id)
            else:
                dest.loc2 = current_obj
                dest.loc1 = Location.get_by_id(dest.loc1.id)
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
        current_obj.destinations = [
            dest for dest in current_obj.destinations
            if dest.bidirectional or dest.loc1.id == current_obj.id
            ]
        from .event import Event
        Event.load_triggers_for_type(id_to_get, cls.typename())
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
            self.grid.dimensions = req.get_numtup(
                'dimensions', (0, 0), delim='x')
            self.grid.excluded = (
                req.get_numtup('excluded_left_top', (0, 0)) +
                req.get_numtup('excluded_right_bottom', (0, 0))
                )
            self.grid.set_default_pos()
            self.destinations = []
            for dest_id, dist, dest_door, other_door, oneway, is_loc1 in zip(
                    req.get_list('other_loc_id[]'),
                    req.get_list('distance[]'),
                    req.get_list('door_here[]'),
                    req.get_list('other_door[]'),
                    req.get_list('oneway[]'),
                    req.get_list('is_loc1[]')
                    ):
                locs = [self.id, dest_id]
                doors = [dest_door, other_door]
                if is_loc1 != 'true':
                    locs.reverse()
                    doors.reverse()
                dest = Destination(Location(locs[0]), Location(locs[1]))
                dest.door1 = NumTup.from_str(doors[0], (0, 0))
                dest.door2 = NumTup.from_str(doors[1], (0, 0))
                dest.distance = int(dist)
                dest.bidirectional = not int(oneway)
                self.destinations.append(dest)
            self.items = {}
            old = Location.load_complete_objects(self.id)
            for item_id, item_qty, item_pos in zip(
                    req.get_list('item_id[]'),
                    req.get_list('item_qty[]'),
                    req.get_list('item_pos[]')
                    ):
                item_at = ItemAt(Item(int(item_id)), self)
                item_at.position = NumTup.from_str(item_pos, (0, 0))
                old_item = old.items.get(item_id, None)
                old_qty = old_item.quantity if old_item else 0
                item_at.quantity = req.set_num_if_changed(item_qty, old_qty)
                self.items[item_id] = item_at
            self.to_db()
        elif req.has_key('delete_location'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed location.'
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")

class Grid:
    def __init__(self):
        self.dimensions = NumTup((0, 0))  # width, height
        self.excluded = NumTup((0, 0, 0, 0))  # left, top, right, bottom
        self.default_pos = None  # legal position in grid if any

    def set_default_pos(self):
        """Returns None if there are no legal positions.
        Call this method whenever changing dimensions or excluded.
        """
        width, height = self.dimensions.as_tuple()
        left, top, right, bottom = self.excluded.as_tuple()
        for y in range(1, height + 1):
            for x in range(1, width + 1):
                if not (left <= x <= right and top <= y <= bottom):
                    self.default_pos = NumTup((x, y))
                    return
        self.default_pos = None

    def in_grid(self, pos):
        """Returns True if position is legally in the grid."""
        if not pos:
            return False
        x, y = pos
        width, height = self.dimensions.as_tuple()
        left, top, right, bottom = self.excluded.as_tuple()
        if x < 1 or x > width:
            return False
        if y < 1 or y > height:
            return False
        if left <= x <= right and top <= y <= bottom:
            return False
        return True
