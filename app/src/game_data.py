from flask import g

from .db_serializable import Identifiable

from src.attrib import Attrib
from src.character import Character
from src.event import Event
from src.item import Item
from src.location import Location
from src.overall import Overall

class GameData:
    # In this order for from_json().
    ENTITIES = [
            Attrib,
            Location,
            Item,
            Character,
            Event]

    def __init__(self):
        g.game_data = self
        for entity_cls in self.ENTITIES:
            self.set_list(entity_cls, [])
        self.overall = Overall()

    def get_list(self, entity_cls):
        return getattr(self, entity_cls.listname)

    def set_list(self, entity_cls, newval):
        setattr(self, entity_cls.listname, newval)

    def to_json(self):
        data = {}
        for entity_cls in self.ENTITIES:
            entity_data = [
                entity.to_json()
                for entity in self.get_list(entity_cls)]
            data[entity_cls.listname] = entity_data
        data['overall'] = self.overall.to_json()
        return data

    @classmethod
    def from_json(cls, data):
        instance = cls()
        # Load in order to correctly get references to other entities. 
        for entity_cls in cls.ENTITIES:
            entity_data = data[entity_cls.listname]
            instance.set_list(
                entity_cls, entity_cls.list_from_json(entity_data))
        instance.overall = Overall.from_json(data['overall'])
        return instance

    @classmethod
    def from_db(cls):
        if 'game_data' in g:
            print("game data already loaded")
            return g.game_data
        print("loading all game data from db")
        instance = cls()
        for entity_cls in cls.ENTITIES:
            instance.set_list(
                entity_cls, entity_cls.list_from_db())
        instance.overall = Overall.from_db()
        return instance

    def to_db(self):
        for entity_cls in self.ENTITIES:
            for entity in self.get_list(entity_cls):
                entity.to_db()
        self.overall.to_db()

    @staticmethod
    def clear_db_for_token():
        for entity_cls in GameData.ENTITIES + [Overall]:
            query = {'game_token': g.game_token}
            table = entity_cls.get_table()
            table.delete_many(query)
