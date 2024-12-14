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

class PriorityDict:
    """A collection that consists of two dictionaries: primary and fallback.
    Store more complete data in the primary dict.
    """
    def __init__(self):
        self.primary = {}
        self.fallback = {}

    def __iter__(self):
        for value in self.primary.values():
            yield value
        for key, value in self.fallback.items():
            if key not in self.primary:
                yield value

    def __getitem__(self, key):
        if key in self.primary:
            return self.primary[key]
        elif key in self.fallback:
            return self.fallback[key]
        else:
            raise KeyError(key)

    def get(self, key, default=None):
        return self.primary.get(key, self.fallback.get(key, default))

    def __delitem__(self, key):
        for data in [self.primary, self.fallback]:
            if key in data:
                del data[key]

    def __len__(self):
        # Not completely accurate but good enough for boolean testing.
        return len(self.primary) + len(self.fallback)

class GameData:
    """Store complete sets of data such as for file export,
    the configure index, or select boxes on forms.
    """
    ENTITIES = ENTITIES  # easier to reference from outside this class
    def __init__(self):
        g.game_data = self
        self.overall = Overall()
        for entity_cls in ENTITIES:
            self.clear_coll(entity_cls)

    def __getitem__(self, key):
        """Allow attributes to be read with square bracket notation,
        easier for Jinja."""
        return getattr(self, key)

    @classmethod
    def entity_for(cls, collname):
        for entity_cls in ENTITIES:
            if entity_cls.collname() == collname:
                return entity_cls
        return None

    def set_coll(self, entity_cls, newval):
        setattr(self, entity_cls.collname(), newval)

    def clear_coll(self, entity_cls):
        self.set_coll(entity_cls, PriorityDict())

    def get_coll(self, entity_cls):
        return getattr(self, entity_cls.collname())

    def set_primary_dict(self, entity_cls, newval):
        data = self.get_coll(entity_cls)
        data.primary = newval

    def set_fallback_dict(self, entity_cls, newval):
        data = self.get_coll(entity_cls)
        data.fallback = newval

    def get_primary_dict(self, entity_cls):
        coll = self.get_coll(entity_cls)
        return coll.primary

    def get_fallback_dict(self, entity_cls):
        coll = self.get_coll(entity_cls)
        return coll.fallback

    def dict_for_json(self):
        logger.debug("dict_for_json()")
        data = {'overall': self.overall.dict_for_json()}
        for entity_cls in ENTITIES:
            entity_data = [
                entity_obj.dict_for_json()
                for entity_obj in self.get_coll(entity_cls)]
            data[entity_cls.collname()] = entity_data
        return data

    def from_json(self, all_data):
        """Load all data from file."""
        logger.debug("from_json()")
        self.overall = Overall.from_data(all_data['overall'])
        for entity_cls in ENTITIES:
            self.clear_coll(entity_cls)
            entities_data = all_data[entity_cls.collname()]
            for entity_data in entities_data:
                instance = entity_cls.from_data(entity_data)
                coll = self.get_coll(entity_cls)
                coll.primary[instance.id] = instance

    def load_for_file(self, entities=None):
        logger.debug("load_complete()")
        self.overall = Overall.load_complete_object()
        if entities is None:
            entities = ENTITIES
        for entity_cls in entities:
            entity_cls.load_complete_objects()

    def from_db_flat(self, entities=None):
        """Don't include entity relation data, just the base tables."""
        logger.debug("from_db_flat()")
        if entities is None:
            entities = ENTITIES
        for entity_cls in entities:
            coll = self.get_coll(entity_cls)
            coll.fallback = {}
            rows = entity_cls.execute_select("""
                SELECT *
                FROM {table}
                WHERE game_token = %s
                ORDER BY name
                """, (g.game_token,))
            for row in rows:
                coll.fallback[row.id] = entity_cls.from_data(row)

    def entity_names_from_db(self, entities=None):
        logger.debug("entity_names_from_db()")
        if entities is None:
            entities = ENTITIES
        query_parts = []
        for entity_cls in entities:
            coll = self.get_coll(entity_cls)
            coll.fallback = {}
            query_parts.append(f"""
                SELECT id, name, '{entity_cls.tablename()}' AS tablename
                FROM {entity_cls.tablename()}
                WHERE game_token = '{g.game_token}'
                """)
        rows = DbSerializable.execute_select(
            " UNION ".join(query_parts) + " ORDER BY name")
        for row in rows:
            entity_cls = self.entity_for(row.tablename)
            coll = self.get_coll(entity_cls)
            coll.fallback[row.id] = entity_cls.from_data(row)

    def to_db(self):
        logger.debug("to_db()")
        self.overall.to_db()
        for entity_cls in ENTITIES:
            logger.debug("entity %s", entity_cls.collname())
            for entity in self.get_coll(entity_cls):
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
