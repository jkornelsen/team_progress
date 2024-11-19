import logging

from flask import g

from .attrib import Attrib
from .character import Character
from .db_serializable import DbSerializable
from .event import Event
from .item import Item
from .location import Location
from .overall import Overall
from .progress import Progress

logger = logging.getLogger(__name__)

# In this order for from_data() to correctly get references to other entities.
ENTITIES = (
    Attrib,
    Item,
    Location,
    Character,
    Event
    )

class ActiveData:
    """Use instead of GameData when the need is to store a set of
    objects loaded or partly loaded for the current task.
    """
    def __init__(self):
        g.active = self
        for entity_cls in ENTITIES:
            setattr(self, entity_cls.listname(), {})

class GameData:
    """Store complete sets of data such as for file export,
    the configure index, or select boxes on forms.
    """
    ENTITIES = ENTITIES  # easier to reference from outside this class
    def __init__(self):
        g.game_data = self
        ActiveData()
        self.overall = Overall()
        for entity_cls in ENTITIES:
            self.set_list(entity_cls, [])

    def __getitem__(self, key):
        """Allow attributes to be read with square bracket notation,
        easier for Jinja."""
        return getattr(self, key)

    @classmethod
    def entity_for(cls, listname):
        for entity_cls in ENTITIES:
            if entity_cls.listname() == listname:
                return entity_cls
        return None

    def get_list(self, entity_cls):
        return getattr(self, entity_cls.listname())

    def set_list(self, entity_cls, newval):
        setattr(self, entity_cls.listname(), newval)

    def dict_for_json(self):
        logger.debug("dict_for_json()")
        data = {'overall': self.overall.dict_for_json()}
        for entity_cls in ENTITIES:
            entity_data = [
                entity_obj.dict_for_json()
                for entity_obj in self.get_list(entity_cls)]
            data[entity_cls.listname()] = entity_data
        return data

    def from_json(self, all_data):
        """Load all data from file."""
        logger.debug("from_json()")
        self.overall = Overall.from_data(all_data['overall'])
        for entity_cls in ENTITIES:
            self.set_list(entity_cls, [])
            entities_data = all_data[entity_cls.listname()]
            for entity_data in entities_data:
                instance = entity_cls.from_data(entity_data)
                self.get_list(entity_cls).append(instance)

    def load_for_file(self, entities=None):
        logger.debug("load_complete()")
        self.overall = Overall.load_complete_object()
        if entities is None:
            entities = ENTITIES
        for entity_cls in entities:
            self.set_list(
                entity_cls, entity_cls.load_complete_objects())

    def from_db_flat(self, entities=None):
        """Don't include entity relation data, just the base tables."""
        logger.debug("from_db_flat()")
        if entities is None:
            entities = ENTITIES
        for entity_cls in entities:
            self.set_list(entity_cls, [])
            rows = entity_cls.execute_select("""
                SELECT *
                FROM {table}
                WHERE game_token = %s
                ORDER BY name
                """, (g.game_token,))
            for row in rows:
                entity = entity_cls.from_data(row)
                self.get_list(entity_cls).append(entity)

    def entity_names_from_db(self, entities=None):
        logger.debug("entity_names_from_db()")
        if entities is None:
            entities = ENTITIES
        query_parts = []
        for entity_cls in entities:
            self.set_list(entity_cls, [])
            query_parts.append(f"""
                SELECT id, name, '{entity_cls.tablename()}' AS tablename
                FROM {entity_cls.tablename()}
                WHERE game_token = '{g.game_token}'
                """)
        rows = DbSerializable.execute_select(
            " UNION ".join(query_parts) + " ORDER BY name")
        for row in rows:
            entity_cls = self.entity_for(row.tablename)
            entity = entity_cls.from_data(row)
            self.get_list(entity_cls).append(entity)

    def to_db(self):
        logger.debug("to_db()")
        self.overall.to_db()
        for entity_cls in ENTITIES:
            logger.debug("entity %s", entity_cls.listname())
            for entity in self.get_list(entity_cls):
                entity.to_db()

    @staticmethod
    def clear_db_for_token():
        logger.debug("clear_db_for_token()")
        tablenames = [
            entity_cls.tablename()
            for entity_cls in list(ENTITIES) + [Overall, Progress]
            ]
        for tablename in tablenames:
            DbSerializable.execute_change(f"""
                DELETE FROM {tablename}
                WHERE game_token = %s
                """, (g.game_token,))
