from collections import namedtuple
import logging

from flask import g

from .attrib import Attrib
from .character import Character
from .cache import cache
from .db_serializable import DbSerializable, Identifiable, QueryHelper, coldef
from .item import Item
from .location import Location
from .utils import RequestHelper

tables_to_create = {
    'overall': f"""
        {coldef('game_token')},
        title varchar(255) NOT NULL,
        {coldef('description')},
        number_format VARCHAR(5) NOT NULL,
        slots TEXT[],
        PRIMARY KEY (game_token)
        """
}
logger = logging.getLogger(__name__)

class WinRequirement(Identifiable):
    """One of:
        * Items with qty and Attrib, at Location or Character
        * Characters with Attrib at Location
    """
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.item = None
        self.quantity = 0
        self.character = None
        self.location = None
        self.attrib = None
        self.attrib_value = 0
        self.fulfilled = False  # is the condition met

    @classmethod
    def tablename(cls):
        return 'win_requirements'

    def _base_export_data(self):
        return {
            'id': self.id,
            'item_id': self.item.id if self.item else None,
            'quantity': self.quantity,
            'char_id': self.character.id if self.character else None,
            'loc_id': self.location.id if self.location else None,
            'attrib_id': self.attrib.id if self.attrib else None,
            'attrib_value': self.attrib_value
            }

    @classmethod
    def from_data(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.item = Item(int(data['item_id'])
            ) if data['item_id'] else None
        instance.quantity = data.get('quantity', 0)
        instance.character = Character(int(data['char_id'])
            ) if data['char_id'] else None
        instance.location = Location(int(data['loc_id'])
            ) if data['loc_id'] else None
        instance.attrib = Attrib(int(data['attrib_id'])
            ) if data['attrib_id'] else None
        instance.attrib_value = data.get('attrib_value', 0)
        return instance

    def id_to_refs_from_game_data(self):
        logger.debug("id_to_refs_from_game_data()")
        for entity_cls in (Item, Character, Location, Attrib):
            attr_name = entity_cls.basename()
            entity = getattr(self, attr_name)
            if entity is not None:
                entity_obj = entity_cls.get_by_id(entity.id)
                if entity_obj:
                    setattr(self, attr_name, entity_obj)
                    logger.debug("%s %d name %s",
                        attr_name, entity.id, entity_obj.name)
                else:
                    logger.debug("could not find %s %d", attr_name, entity.id)

class Overall(DbSerializable):
    """Overall scenario settings such as scenario title and goal,
    and app settings."""

    def __init__(self):
        super().__init__()
        self.title = "Generic Adventure"
        self.description = (
            "Go to <i>Main Setup</i> (or press M)."
            " To continue a game you've already started, click <i>Load from File</i>."
            " Otherwise, browse the <i>Pre-Built Scenarios</i>."
            "\r\n\r\n"
            "Or, to start from scratch, go to <i>Overall Settings</i>"
            " and change the title and this description."
            " Then do initial setup such as adding a few items or characters."
            " More can be added as the game goes along."
            )
        self.win_reqs = []
        self.number_format = 'en_US'
        self.slots = ["Main Hand", "Off Hand", "Body Armor"]

    @classmethod
    def tablename(cls):
        return 'overall'

    def have_won(self):
        if not self.win_reqs:
            return False
        for win_req in self.win_reqs:
            if not win_req.fulfilled:
                return False
        return True

    def load_cached(self):
        self.number_format = self._cached_number_format()

    @classmethod
    def invalidate_cached(cls):
        cache.delete_memoized(cls._cached_number_format)

    @classmethod
    @cache.memoize(timeout=600)
    def _cached_number_format(cls):
        row = cls.execute_select("""
            SELECT number_format
            FROM overall
            WHERE game_token = %s
            """, (g.game_token,), fetch_all=False)
        if row:
            return row.number_format
        return ''

    def _base_export_data(self):
        return {
            'title': self.title,
            'description': self.description,
            'number_format': self.number_format,
            'slots': self.slots,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'win_reqs': [
                win_req.dict_for_json()
                for win_req in self.win_reqs],
            })
        return data

    @classmethod
    def from_data(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls()
        instance.title = data.get(
            'title', instance.title)
        instance.description = data.get(
            'description', instance.description)
        instance.win_reqs = [
            WinRequirement.from_data(winreq_data)
            for winreq_data in data.get('win_reqs', [])]
        instance.number_format = data.get(
            'number_format', instance.number_format)
        instance.slots = list(data.get('slots') or [])
        return instance

    @classmethod
    def load_complete_object(cls):
        """Load everything needed to serialize the Overall object."""
        logger.debug("load_complete_objects()")
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        tables_rows = cls.select_tables(
            qhelper=qhelper, tables=['overall', 'win_requirements'])
        instance = None
        for overall_data, winreq_data in tables_rows:
            if not instance:
                instance = cls.from_data(vars(overall_data))
            if winreq_data.item_id or winreq_data.char_id:
                instance.win_reqs.append(
                    WinRequirement.from_data(winreq_data))
        if not instance:
            logger.debug("overall data not found -- making generic object")
            instance = cls()
        return instance

    def to_db(self):
        super().to_db()
        self.execute_change("""
            DELETE FROM win_requirements
            WHERE game_token = %s
            """, (g.game_token,))
        for win_req in self.win_reqs:
            win_req.to_db()

    @classmethod
    def data_for_configure(cls):
        logger.debug("data_for_configure()")
        g.game_data.entity_names_from_db()
        g.game_data.overall = cls.load_complete_object()
        for win_req in g.game_data.overall.win_reqs:
            win_req.id_to_refs_from_game_data()

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes'):
            req.debug()
            self.title = req.get_str('scenario_title')
            self.description = req.get_str('scenario_description')
            winreq_ids = req.get_list('winreq_id')
            self.win_reqs = []
            for winreq_id in winreq_ids:
                prefix = f"winreq{winreq_id}_"
                winreq = WinRequirement()
                self.win_reqs.append(winreq)
                winreq.quantity = req.get_float(f"{prefix}quantity")
                item_id = req.get_int(f"{prefix}item_id")
                if item_id:
                    winreq.item = Item(item_id)
                char_id = req.get_int(f"{prefix}char_id")
                if char_id:
                    winreq.character = Character(char_id)
                loc_id = req.get_int(f"{prefix}loc_id")
                if loc_id:
                    winreq.location = Location(loc_id)
                attrib_id = req.get_int(f"{prefix}attrib_id")
                if attrib_id:
                    winreq.attrib = Attrib(attrib_id)
                winreq.attrib_value = req.get_float(f"{prefix}attribValue")
            self.number_format = req.get_str('number_format')
            self.slots = [
                slot.strip()
                for slot in req.get_str('slots').splitlines()
                if slot.strip()]
            self.to_db()
            self.invalidate_cached()
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")

    @classmethod
    def data_for_overview(cls):
        logger.debug("data_for_overview()")
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.location_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
                AND {tables[0]}.toplevel = TRUE
            ORDER BY {tables[0]}.name
            """, (g.game_token,), ['characters', 'locations'])
        CharacterRow = namedtuple('CharacterRow',
            ['char_id', 'char_name', 'loc_id', 'loc_name'])
        character_list = []
        for char_data, loc_data in tables_rows:
            row = CharacterRow(
                char_id=char_data.id,
                char_name=char_data.name,
                loc_id=loc_data.id,
                loc_name=loc_data.name)
            character_list.append(row)
        logger.debug("character_list=%s", character_list)
        g.active.characters = character_list
        loc_rows = cls.execute_select("""
            SELECT id, name
            FROM locations
            WHERE toplevel = TRUE
                AND game_token = %s
            ORDER BY name
            """, (g.game_token,))
        g.active.locations = loc_rows
        item_rows = cls.execute_select("""
            SELECT id, name
            FROM items
            WHERE toplevel = TRUE
                AND game_token = %s
            ORDER BY name
            """, (g.game_token,))
        g.active.items = item_rows
        event_rows = cls.execute_select("""
            SELECT id, name
            FROM events
            WHERE toplevel = TRUE
                AND game_token = %s
            ORDER BY name
            """, (g.game_token,))
        g.active.events = event_rows
        instance = cls.load_complete_object()
        g.active.overall = instance
        # Win requirement results
        req_by_id = {}
        for win_req in instance.win_reqs:
            win_req.id_to_refs_from_game_data()
            req_by_id[win_req.id] = win_req
        NUM_QUERIES = 5
        rows = cls.execute_select("""
            SELECT A.id  -- item general storage
            FROM win_requirements A, items B
            WHERE A.game_token = %s
                AND A.char_id IS NULL
                AND A.loc_id IS NULL
                AND B.game_token = A.game_token
                AND B.id = A.item_id
                AND B.quantity >= A.quantity
            UNION
            SELECT A.id  -- item at location
            FROM win_requirements A, loc_items B
            WHERE A.game_token = %s
                AND A.char_id IS NULL
                AND B.game_token = A.game_token
                AND B.loc_id = A.loc_id
                AND B.item_id = A.item_id
                AND B.quantity >= A.quantity
            UNION
            SELECT A.id  -- item owned by character at location
            FROM win_requirements A, characters B, char_items C
            WHERE A.game_token = %s
                AND A.loc_id IS NOT NULL
                AND B.game_token = A.game_token
                AND B.location_id = A.loc_id
                AND C.game_token = A.game_token
                AND C.char_id = B.id
                AND C.item_id = A.item_id
                AND C.quantity >= A.quantity
            UNION
            SELECT A.id  -- character at location
            FROM win_requirements A, characters B
            WHERE A.game_token = %s
                AND A.item_id IS NULL
                AND A.loc_id IS NOT NULL
                AND B.game_token = A.game_token
                AND B.id = A.char_id
                AND B.location_id = A.loc_id
            UNION
            SELECT A.id  -- character with attribute
            FROM win_requirements A, char_attribs B
            WHERE A.game_token = %s
                AND A.item_id IS NULL
                AND A.attrib_id IS NOT NULL
                AND B.game_token = A.game_token
                AND B.char_id = A.char_id
                AND B.attrib_id = A.attrib_id
                AND B.value >= A.attrib_value
            """, (g.game_token,) * NUM_QUERIES)
        for row in rows:
            win_req = req_by_id.get(row.id)
            win_req.fulfilled = True
        return g.active
