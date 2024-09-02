from flask import g
import logging

from .db_serializable import DbSerializable
from .db_relations import tables_to_create as relation_tables

from .attrib import Attrib
from .character import Character
from .event import Event
from .item import Item, Recipe
from .location import Location
from .overall import Overall
from .progress import Progress

logger = logging.getLogger(__name__)

class GameData:
    # In this order for from_json().
    ENTITIES = (
            Attrib,
            Item,
            Location,
            Character,
            Event)

    def __init__(self):
        g.game_data = self
        for entity_cls in self.ENTITIES:
            self.set_list(entity_cls, [])
        self.overall = Overall()

    def __getitem__(self, key):
        """Allow attributes to be read with square bracket notation,
        easier for Jinja."""
        return getattr(self, key)

    @classmethod
    def entity_for(cls, listname):
        for entity_cls in cls.ENTITIES:
            if entity_cls.listname() == listname:
                return entity_cls
        return None

    def get_list(self, entity_cls):
        return getattr(self, entity_cls.listname())

    def set_list(self, entity_cls, newval):
        setattr(self, entity_cls.listname(), newval)

    def to_json(self):
        logger.debug("to_json()")
        data = {}
        for entity_cls in self.ENTITIES:
            entity_data = [
                entity_obj.to_json()
                for entity_obj in self.get_list(entity_cls)]
            data[entity_cls.listname()] = entity_data
        data['overall'] = self.overall.to_json()
        return data

    @classmethod
    def from_json(cls, data):
        logger.debug("from_json()")
        instance = cls()
        # Load in order to correctly get references to other entities. 
        for entity_cls in cls.ENTITIES:
            entity_data = data[entity_cls.listname()]
            instance.set_list(
                entity_cls, entity_cls.list_from_json(entity_data))
        instance.overall = Overall.from_json(data['overall'])
        return instance

    @classmethod
    def from_db(cls, entities=None):
        logger.debug("from_db()")
        if entities is None:
            entities = cls.ENTITIES
            instance = cls()
        else:
            instance = g.game_data
        for entity_cls in entities:
            instance.set_list(
                entity_cls, entity_cls.list_from_db())
        instance.overall = Overall.from_db()
        return instance

    @classmethod
    def entity_names_from_db(cls, entities=None):
        logger.debug("entity_names_from_db()")
        if entities is None:
            entities = cls.ENTITIES
            instance = cls()
        else:
            instance = g.game_data
        query_parts = []
        for entity_cls in entities:
            instance.set_list(entity_cls, [])
            query_parts.append(f"""
                SELECT id, name, '{entity_cls.tablename()}' AS tablename
                FROM {entity_cls.tablename()}
                WHERE game_token = '{g.game_token}'
            """)
        rows = DbSerializable.execute_select(
            " UNION ".join(query_parts) + " ORDER BY name")
        for row in rows:
            entity_cls = instance.entity_for(row.tablename)
            entity = entity_cls.from_json(row)
            instance.get_list(entity_cls).append(entity)
        return instance

    def to_db(self):
        logger.debug("to_db()")
        for entity_cls in self.ENTITIES:
            logger.debug("entity %s", entity_cls.listname())
            for entity in self.get_list(entity_cls):
                entity.to_db()
        self.overall.to_db()

    @staticmethod
    def clear_db_for_token():
        logger.debug("clear_db_for_token()")
        tablenames = [
                tablename
                for tablename in relation_tables
            ] + [
                entity_cls.tablename()
                for entity_cls in [Recipe
                    ] + list(GameData.ENTITIES) + [Overall, Progress]
            ]
        for tablename in tablenames:
            DbSerializable.execute_change(f"""
                DELETE FROM {tablename}
                WHERE game_token = %s
            """, (g.game_token,))

