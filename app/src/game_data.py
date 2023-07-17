from src.attrib import Attrib
from src.character import Character
from src.event import Event
from src.item import Item
from src.location import Location
from src.overall import Overall

class GameData:
    def __init__(self):
        self.attribs = Attrib.instances
        self.characters = Character.instances
        self.events = Event.instances
        self.items = Item.instances
        self.locations = Location.instances
        self.overall = Overall.from_db()
        Attrib.game_data = self
        Character.game_data = self
        Event.game_data = self
        Item.game_data = self
        Location.game_data = self
        Overall.game_data = self

    def to_json(self):
        return {
            'attribs': [attrib.to_json() for attrib in self.attribs],
            'locations': [location.to_json() for location in self.locations],
            'items': [item.to_json() for item in self.items],
            'characters': [character.to_json() for character in self.characters],
            'events': [event.to_json() for event in self.events],
            'overall': self.overall.to_json()
        }

    @classmethod
    def from_json(cls, data):
        instance = cls()
        # Load in this order to correctly get references to other entities. 
        instance.attribs = Attrib.list_from_json(data['attribs'])
        instance.locations = Location.list_from_json(data['locations'])
        instance.items = Item.list_from_json(data['items'])
        instance.characters = Character.list_from_json(data['characters'])
        instance.events = Event.list_from_json(data['events'])
        instance.overall = Overall.from_json(data['overall'])
        return instance

    def to_db(self):
        entity_lists = [
            self.attribs, self.locations, self.items, self.characters,
            self.events]
        for entity_list in entity_lists:
            for entity in entity_list:
                entity.to_db()
        self.overall.to_db()

    @classmethod
    def from_db(cls):
        instance = cls()
        instance.attribs = Attrib.list_from_db()
        instance.locations = Location.list_from_db()
        instance.items = Item.list_from_db()
        instance.characters = Character.list_from_db()
        instance.events = Event.list_from_db()
        instance.overall = Overall.from_db()
        return instance

