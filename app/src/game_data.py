from flask import g

from src.attrib import Attrib
from src.character import Character
from src.event import Event
from src.item import Item
from src.location import Location
from src.overall import Overall

# In this order for from_json().
ENTITIES = [
        Attrib,
        Location,
        Item,
        Character,
        Event]

def entity_name(cls):
    return cls.__name__.lower() + 's'

class GameData:
    def __init__(self):
        for entity_cls in ENTITIES:
            entity_cls.instances.clear()
            setattr(self, entity_name(entity_cls), entity_cls.instances)
            setattr(entity_cls, 'game_data', self)
        self.overall = Overall.from_db()
        Overall.game_data = self

    def to_json(self):
        game_data = {}
        for entity_cls in ENTITIES:
            entity_data = [
                entity.to_json()
                for entity in getattr(self, entity_name(entity_cls))]
            game_data[entity_name(entity_cls)] = entity_data
        game_data['overall'] = self.overall.to_json()
        return game_data

    @classmethod
    def from_json(cls, data):
        instance = cls()
        # Load in order to correctly get references to other entities. 
        for entity_cls in ENTITIES:
            entity_data = data[entity_name(entity_cls)]
            setattr(
                instance, entity_name(entity_cls),
                entity_cls.list_from_json(entity_data))
        instance.overall = Overall.from_json(data['overall'])
        return instance

    def to_db(self):
        for entity_cls in ENTITIES:
            entity_list = getattr(self, entity_name(entity_cls))
            for entity in entity_list:
                entity.to_db()
        self.overall.to_db()

    @staticmethod
    def clear_db_for_token():
        for entity_cls in ENTITIES + [Overall]:
            query = {'game_token': g.game_token}
            collection = entity_cls.get_collection()
            collection.delete_many(query)

    @classmethod
    def from_db(cls):
        instance = cls()
        for entity_cls in ENTITIES:
            setattr(
                instance, entity_name(entity_cls),
                entity_cls.list_from_db())
        instance.overall = Overall.from_db()
        return instance

