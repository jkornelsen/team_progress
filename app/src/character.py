import logging

from flask import g, session

from .attrib import Attrib, AttribFor
from .db_serializable import (
    DbError, DeletionError, Identifiable, QueryHelper, Serializable, coldef)
from .item import Item
from .location import Destination, Location
from .pile import Pile
from .progress import Progress
from .utils import NumTup, RequestHelper

tables_to_create = {
    'characters': f"""
        {coldef('name')},
        toplevel boolean NOT NULL,
        masked boolean NOT NULL,
        progress_id integer,
        quantity float(4) NOT NULL,
        location_id integer,
        dest_id integer,
        position integer[2],
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
            DEFERRABLE INITIALLY DEFERRED
        """,
}
logger = logging.getLogger(__name__)

class OwnedItem(Pile):
    def __init__(self, item, char):
        super().__init__(item=item, container=char)
        self.slot = ''  # for example, "main hand"

    @classmethod
    @property
    def container_type(cls):
        return Character.typename

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

class Character(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = True
        self.masked = False
        self.attribs = {}  # AttribFor objects keyed by attr id
        self.items = {}  # OwnedItem objects keyed by item id
        self.location = None  # Location object where char is
        self.position = NumTup((0, 0))
        self.destination = None  # Location object to travel to
        self.progress = Progress(container=self)  # travel or producing items
        self.pile = Pile(container=self)  # for Progress

    @classmethod
    @property
    def typename(cls):
        return 'char'

    def _base_export_data(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'masked': self.masked,
            'location_id': self.location.id if self.location else None,
            'quantity': self.pile.quantity,
            'dest_id': self.destination.id if self.destination else None
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'attribs': [
                attrib_for.as_tuple()
                for attrib_for in self.attribs.values()],
            'items': [
                owned.dict_for_json() for owned in self.items.values()],
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
        for owned_data in data.get('items', []):
            try:
                if not isinstance(owned_data, dict):
                    owned_data = vars(owned_data)
                item_id = owned_data.get('item_id', 0)
                instance.items[item_id] = OwnedItem.from_data(
                    owned_data, instance)
            except TypeError:
                logger.exception('')
                continue
        instance.attribs = {
            attrib_id: AttribFor(attrib_id, val)
            for attrib_id, val in data.get('attribs', [])}
        instance.location = Location(
            int(data['location_id'])) if data.get('location_id', 0) else None
        instance.position = NumTup.from_list(data.get('position', [0, 0]))
        instance.pile.quantity = data.get('quantity', 0.0)
        instance.progress = Progress.from_data(
            data.get('progress', {}), instance)
        instance.destination = Location(
            int(data['dest_id'])) if data.get('dest_id', 0) else None
        return instance

    def to_db(self):
        logger.debug("to_db()")
        self.progress.to_db()
        super().to_db()
        for rel_table in ('char_attribs', 'char_items'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE char_id = %s AND game_token = %s
                """, (self.id, self.game_token))
        if self.attribs:
            values = []
            for attrib_for in self.attribs.values():
                values.append((
                    g.game_token, self.id,
                    attrib_for.attrib_id,
                    attrib_for.val
                    ))
            self.insert_multiple(
                "char_attribs",
                "game_token, char_id, attrib_id, value",
                values)
        if self.items:
            logger.debug("items: %s", self.items)
            values = []
            for item_id, owned_item in self.items.items():
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
        chars = []
        characters_rows = cls.execute_select(query, values)
        for char_row in characters_rows:
            chars.append(Character.from_data(char_row))
        if load:
            g.game_data.set_list(Character, chars)
        return chars

    @classmethod
    def load_characters_for_event(cls, event_id, load=True):
        """Get characters that have attributes used for the event."""
        characters_rows = cls.execute_select("""
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.char_id = {tables[0]}.id
            INNER JOIN {tables[2]}
                ON {tables[2]}.game_token = {tables[0]}.game_token
                AND {tables[2]}.event_id = %s
                AND {tables[2]}.attrib_id = {tables[1]}.attrib_id
            WHERE {tables[0]}.game_token = %s
            """, (event_id, g.game_token),
            ['characters', 'char_attribs', 'event_entities'])
        chars = {}
        for char_row in characters_rows:
            if char_row.id not in chars:
                chars[char_row.id] = Character.from_data(char_row)
        # Get char attrib data
        attrib_rows = cls.execute_select("""
            SELECT *
            FROM char_attribs
            WHERE game_token = %s
            """, [g.game_token])
        for row in attrib_rows:
            char = chars.get(row.char_id, None)
            if char:
                # XXX: Couldn't we have done this in the first query?
                char.attribs[row.attrib_id] = AttribFor.from_data(row)
        if load:
            g.game_data.set_list(Character, chars.values())
        return chars

    @classmethod
    def load_complete_objects(cls, id_to_get=None):
        logger.debug("load_complete_objects(%s)", id_to_get)
        if id_to_get in ['new', '0', 0]:
            return cls()
        chars = Progress.load_base_data(cls, id_to_get)
        # Get attrib relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM char_attribs
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("char_id", id_to_get)
        attrib_rows = cls.execute_select(qhelper=qhelper)
        for row in attrib_rows:
            char = chars[row.char_id]
            char.setdefault(
                'attribs', []).append((row.attrib_id, row.value))
        # Get item relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM char_items
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("char_id", id_to_get)
        item_rows = cls.execute_select(qhelper=qhelper)
        for row in item_rows:
            char = chars[row.char_id]
            char.setdefault('items', []).append(row)
        # Set list of objects
        instances = []
        for data in chars.values():
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
        g.game_data.from_db_flat([Attrib, Location, Item])
        for attrib_id, attrib_for in current_obj.attribs.items():
            attrib_for.attrib = Attrib.get_by_id(attrib_id)
        for item_id, owned_item in current_obj.items.items():
            owned_item.item = Item.get_by_id(item_id)
        if current_obj.location:
            current_obj.location = Location.get_by_id(
                current_obj.location.id)
        if current_obj.destination:
            current_obj.destination = Location.get_by_id(
                current_obj.destination.id)
        # Print debugging info
        logger.debug("found %d owned items", len(current_obj.items))
        if len(current_obj.items):
            owned_item = next(iter(current_obj.items.values()))
            logger.debug("item_id=%d, name=%s, quantity=%.1f, slot=%s",
                owned_item.item.id, owned_item.item.name, owned_item.quantity,
                owned_item.slot)
        return current_obj

    @classmethod
    def data_for_play(cls, id_to_get):
        logger.debug("data_for_play()")
        current_obj = cls.data_for_configure(id_to_get)
        if current_obj.location:
            current_obj.location.unmask()
            # Get the current location's destination data
            tables_rows = cls.select_tables("""
                SELECT *
                FROM {tables[0]}
                INNER JOIN {tables[1]}
                    ON {tables[1]}.id = {tables[0]}.dest_id
                    AND {tables[1]}.game_token = {tables[0]}.game_token
                WHERE {tables[0]}.game_token = %s
                    AND {tables[0]}.loc_id = %s
                """, [g.game_token, current_obj.location.id],
                ['loc_destinations', 'locations'])
            for dest_data, loc_data in tables_rows:
                dest = Destination.from_data(dest_data)
                dest.loc = Location.from_data(loc_data)
                current_obj.location.destinations[loc_data.id] = dest
            # Travel distance
            if current_obj.destination:
                dest = current_obj.location.destinations.get(
                    current_obj.destination.id, None)
                if dest:
                    current_obj.pile.item.q_limit = dest.distance
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
            self.items = {}
            old = Character.load_complete_objects(self.id)
            for item_id, item_qty, item_slot in zip(
                    req.get_list('item_id[]'),
                    req.get_list('item_qty[]'),
                    req.get_list('item_slot[]')
                    ):
                ownedItem = OwnedItem(Item(int(item_id)), self)
                ownedItem.slot = item_slot
                old_item = old.items.get(item_id, None)
                old_qty = old_item.quantity if old_item else 0
                ownedItem.quantity = req.set_num_if_changed(item_qty, old_qty)
                self.items[item_id] = ownedItem
            location_id = req.get_int('char_location')
            self.location = Location(location_id) if location_id else None
            self.position = req.get_numtup('position', (0, 0))
            attrib_ids = req.get_list('attrib_id[]')
            logger.debug("Attrib IDs: %s", attrib_ids)
            self.attribs = {}
            for attrib_id in attrib_ids:
                attrib_val = req.get_float(f'attrib{attrib_id}_val', 0.0)
                self.attribs[attrib_id] = AttribFor(attrib_id, attrib_val)
            self.to_db()
        elif req.has_key('delete_character'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed character.'
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")
