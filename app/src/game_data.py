from flask import g

from src.attrib import Attrib
from src.character import Character
from src.event import Event
from src.item import Item
from src.location import Location
from src.overall import Overall

def entity_name(entity_cls):
    # attributes of GameData, same as table name
    return "{}s".format(entity_cls.__name__.lower())

class GameData:
    # In this order for from_json().
    ENTITIES = [
            Attrib,
            Location,
            Item,
            Character,
            Event]

    instance = None  # reference to singleton

    def __init__(self):
        for entity_cls in self.ENTITIES:
            entity_cls.instances.clear()
            setattr(self, entity_name(entity_cls), entity_cls.instances)
        self.overall = Overall.instance

    def to_json(self):
        data = {}
        for entity_cls in self.ENTITIES:
            entity_data = [
                entity.to_json()
                for entity in getattr(self, entity_name(entity_cls))]
            data[entity_name(entity_cls)] = entity_data
        data['overall'] = self.overall.to_json()
        return data

    @classmethod
    def from_json(cls, data):
        if cls.instance:
            return cls.instance
        instance = cls()
        # Load in order to correctly get references to other entities. 
        for entity_cls in cls.ENTITIES:
            entity_data = data[entity_name(entity_cls)]
            setattr(
                instance, entity_name(entity_cls),
                entity_cls.list_from_json(entity_data))
        instance.overall = Overall.from_json(data['overall'])
        return instance

    def to_db(self):
        for entity_cls in self.ENTITIES:
            entity_list = getattr(self, entity_name(entity_cls))
            for entity in entity_list:
                entity.to_db()
        self.overall.to_db()

    @staticmethod
    def clear_db_for_token():
        for entity_cls in GameData.ENTITIES + [Overall]:
            query = {'game_token': g.game_token}
            table = entity_cls.get_table()
            table.delete_many(query)

    @classmethod
    def from_db(cls):
        if cls.instance:
            return cls.instance
        instance = cls()
        for entity_cls in cls.ENTITIES:
            setattr(
                instance, entity_name(entity_cls),
                entity_cls.list_from_db())
        instance.overall = Overall.from_db()
        return instance

