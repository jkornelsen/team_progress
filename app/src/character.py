import logging

from flask import g, session, url_for

from .attrib import Attrib, AttribFor
from .db_serializable import (
    DbError, DeletionError, CompleteIdentifiable, QueryHelper, coldef)
from .item import Item
from .location import Location
from .pile import Pile
from .progress import Progress
from .utils import NumTup, RequestHelper

logger = logging.getLogger(__name__)
tables_to_create = {
    'characters': f"""
        {coldef('name')},
        toplevel boolean NOT NULL,  -- show in overview
        masked boolean NOT NULL,  -- hide until met; not implemented
        location_id integer,  -- current location
        position integer[2],  -- position in the grid of the current location
        progress_id integer,  -- id in progress table for any ongoing travel
        dest_id integer,  -- selected location id to go to or in progress
        travel_group varchar(100),  -- selectable for more than one companion
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
            DEFERRABLE INITIALLY DEFERRED
        """,
    }

class OwnedItem(Pile):
    def __init__(self, item, char):
        super().__init__(item=item, container=char)
        self.slot = ''  # for example, "main hand"

    @staticmethod
    def container_type():
        return Character.typename()

    def _base_export_data(self):
        return {
            'item_id': self.item.id,
            'quantity': self.quantity,
            'slot': self.slot,
            }

    @classmethod
    def from_data(cls, data, char=None):
        data = cls.prepare_dict(data)
        instance = cls(None, char)
        instance.set_basic_data(data)
        instance.item = Item(instance.item_id)
        instance.slot = data.get('slot', "")
        return instance

    def equipped(self):
        """If not equipped (worn or held), then it's assumed to be carried in
        inventory, such as a backpack."""
        return bool(self.slot)

class Character(CompleteIdentifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = False
        self.masked = False
        self.travel_group = ""
        self.attribs = {}  # AttribFor objects keyed by attr id
        self.events = []  # Event IDs -- abilities
        self.owned_items = {}  # OwnedItem objects keyed by item id
        self.location = None  # Location object where char is
        self.position = NumTup((0, 0))
        self.dest_loc = None  # Location object to travel to
        self.destination = None  # Destination object
        self.progress = Progress(pholder=self)  # travel or producing items

    @classmethod
    def typename(cls):
        return 'char'

    def _base_export_data(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'masked': self.masked,
            'travel_group': self.travel_group,
            'location_id': self.location.id if self.location else None,
            'dest_id': self.dest_loc.id if self.dest_loc else None
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'attribs': [
                attrib_for.as_tuple()
                for attrib_for in self.attribs.values()],
            'events': self.events,
            'items': [
                owned.dict_for_json() for owned in self.owned_items.values()],
            'progress': self.progress.dict_for_json(),
            'position': self.position.as_list(),
            })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'position': self.position,
            'progress_id': self.progress.id or None,
            })
        return data

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = super().from_data(data)
        instance.toplevel = data.get('toplevel', False)
        instance.masked = data.get('masked', False)
        instance.travel_group = data.get('travel_group', "")
        for owned_data in data.get('items', []):
            try:
                if not isinstance(owned_data, dict):
                    owned_data = vars(owned_data)
                item_id = owned_data.get('item_id', 0)
                instance.owned_items[item_id] = OwnedItem.from_data(
                    owned_data, instance)
            except TypeError:
                logger.exception('')
                continue
        instance.attribs = {
            attrib_id: AttribFor(attrib_id, val)
            for attrib_id, val in data.get('attribs', [])}
        instance.events = data.get('events', [])
        instance.location = Location(
            int(data['location_id'])) if data.get('location_id', 0) else None
        instance.position = NumTup.from_list(data.get('position', [0, 0]))
        instance.progress = Progress.from_data(
            data.get('progress', {}), instance)
        instance.dest_loc = Location(
            int(data['dest_id'])) if data.get('dest_id', 0) else None
        return instance

    def to_db(self):
        logger.debug("to_db()")
        self.progress.to_db()
        super().to_db()
        for rel_table in ('char_items', 'attribs_of', 'event_triggers'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE char_id = %s AND game_token = %s
                """, (self.id, g.game_token))
        if self.attribs:
            values = []
            for attrib_for in self.attribs.values():
                values.append((
                    g.game_token, self.id,
                    attrib_for.attrib_id,
                    attrib_for.val
                    ))
            self.insert_multiple(
                "attribs_of",
                "game_token, char_id, attrib_id, value",
                values)
        if self.events:
            values = [
                (g.game_token, self.id, event_id)
                for event_id in self.events
                ]
            self.insert_multiple(
                "event_triggers",
                "game_token, char_id, event_id", values)
        if self.owned_items:
            logger.debug("items: %s", self.owned_items)
            values = []
            for item_id, owned_item in self.owned_items.items():
                values.append((
                    g.game_token, self.id,
                    item_id,
                    owned_item.quantity,
                    owned_item.slot
                    ))
            self.insert_multiple(
                "char_items",
                "game_token, char_id, item_id, quantity, slot",
                values)

    @classmethod
    def load_characters_at_loc(cls, loc_id, load=True):
        query = """
            SELECT *
            FROM {table}
            WHERE game_token = %s
            """
        values = [g.game_token]
        if loc_id:
            query += " AND location_id = %s"
            values.append(loc_id)
        query += "\nORDER BY name"
        instances = {}
        rows = cls.execute_select(query, values)
        for row in rows:
            instances[row.id] = cls.from_data(row)
        if load:
            cls.get_coll().fallback.update(instances)
        return instances.values()

    def get_destinations(self, dest_id=None):
        if not self.location:
            return
        cur_dest_loc_id = (
            dest_id if dest_id else (
                self.dest_loc.id if self.dest_loc and self.dest_loc.id else 0)
            )
        dests, self.destination = Location.get_destinations_from(
            self.location.id, self.position.as_tuple(), cur_dest_loc_id)
        self.location.destinations = dests

    @classmethod
    def load_travel_groups(cls, loc_id, char_id = 0):
        """Get travel groups for other characters.
        @returns: list of tuples (group name, IDs joined by commas).
        """
        query = """
            SELECT id, travel_group
            FROM {table}
            WHERE game_token = %s
                AND travel_group != ''
                AND location_id = %s
                AND id != %s
            ORDER BY travel_group
            """
        values = [g.game_token, loc_id, char_id]
        rows = cls.execute_select(query, values)
        groups = {}
        for row in rows:
            groups.setdefault(row.travel_group, []).append(row.id)
        return [
            (group_name, ','.join(map(str, ids)))
            for group_name, ids in groups.items()
            if len(ids) >= 2
            ]

    @classmethod
    def load_complete_objects(cls, ids=None):
        logger.debug("load_complete_objects(%s)", ids)
        if cls.empty_values(ids):
            return [cls()]
        chars = Progress.load_base_data_dict(cls, ids)
        # Get attrib relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM attribs_of
            WHERE game_token = %s
                AND char_id IS NOT NULL
            """, [g.game_token])
        qhelper.add_limit_in("char_id", ids)
        attrib_rows = cls.execute_select(qhelper=qhelper)
        for row in attrib_rows:
            char = chars[row.char_id]
            char.setdefault(
                'attribs', []).append((row.attrib_id, row.value))
        # Get data for event links
        qhelper = QueryHelper("""
            SELECT *
            FROM event_triggers
            WHERE game_token = %s
                AND char_id IS NOT NULL
            """, [g.game_token])
        qhelper.add_limit_in("char_id", ids)
        event_rows = cls.execute_select(qhelper=qhelper)
        for row in event_rows:
            char = chars[row.char_id]
            char.setdefault('events', []).append(row.event_id)
        # Get item relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM char_items
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit_in("char_id", ids)
        item_rows = cls.execute_select(qhelper=qhelper)
        for row in item_rows:
            char = chars[row.char_id]
            char.setdefault('items', []).append(row)
        # Set list of objects
        instances = {}
        for data in chars.values():
            instances[data.id] = cls.from_data(data)
        if ids and any(ids) and not instances:
            logger.warning(f"Could not load characters {ids}.")
        cls.get_coll().primary.update(instances)
        return instances.values()

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_object(id_to_get)
        # Replace partial objects with fully populated objects
        from .event import Event
        g.game_data.from_db_flat([Attrib, Event, Location, Item])
        for attrib_id, attrib_for in current_obj.attribs.items():
            attrib_for.attrib = Attrib.get_by_id(attrib_id)
        for item_id, owned_item in current_obj.owned_items.items():
            owned_item.item = Item.get_by_id(item_id)
        if current_obj.location:
            current_obj.location = Location.get_by_id(
                current_obj.location.id)
        if current_obj.dest_loc:
            current_obj.dest_loc = Location.get_by_id(
                current_obj.dest_loc.id)
        # Print debugging info
        logger.debug("found %d owned items", len(current_obj.owned_items))
        if len(current_obj.owned_items):
            owned_item = next(iter(current_obj.owned_items.values()))
            logger.debug("item_id=%d, name=%s, quantity=%.1f, slot=%s",
                owned_item.item.id, owned_item.item.name, owned_item.quantity,
                owned_item.slot)
        return current_obj

    @classmethod
    def data_for_play(cls, id_to_get):
        logger.debug("data_for_play()")
        current_obj = cls.data_for_configure(id_to_get)
        cur_loc = current_obj.location
        if cur_loc:
            cur_loc.unmask()
            current_obj.get_destinations()
            if current_obj.destination:
                current_obj.progress.recipe.rate_duration = (
                    current_obj.destination.duration)
            cls.load_characters_at_loc(cur_loc.id)
        # Abilities
        from .event import Event
        Event.load_triggers_for_type(id_to_get, cls.typename())
        return current_obj

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes') or req.has_key('make_duplicate'):
            req.debug()
            self.name = req.get_str('char_name')
            self.description = req.get_str('char_description')
            req = RequestHelper('form')
            self.toplevel = req.get_bool('top_level')
            self.masked = req.get_bool('masked')
            self.travel_group = req.get_str('travel_group')
            self.owned_items = {}
            old = Character.load_complete_object(self.id)
            for item_id, item_qty, item_slot in zip(
                    req.get_list('item_id[]'),
                    req.get_list('item_qty[]'),
                    req.get_list('item_slot[]'),
                    ):
                ownedItem = OwnedItem(Item(int(item_id)), self)
                ownedItem.slot = item_slot
                old_item = old.owned_items.get(item_id, None)
                old_qty = old_item.quantity if old_item else 0
                ownedItem.quantity = req.set_num_if_changed(
                    item_qty, [old_qty])
                self.owned_items[item_id] = ownedItem
            location_id = req.get_int('char_location')
            self.location = Location(location_id) if location_id else None
            self.position = req.get_numtup('position')
            attrib_ids = req.get_list('attrib_id[]')
            logger.debug("Attrib IDs: %s", attrib_ids)
            self.attribs = {}
            for attrib_id in attrib_ids:
                attrib_val = req.get_float(f'attrib{attrib_id}_val', 0.0)
                self.attribs[attrib_id] = AttribFor(attrib_id, attrib_val)
            self.events = req.get_list('event_id[]')
            self.to_db()
        elif req.has_key('delete_character'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed character.'
                session['referrer'] = url_for('configure_index')
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")
