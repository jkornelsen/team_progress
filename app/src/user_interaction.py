from datetime import datetime, timedelta
from flask import g

from .attrib import Attrib
from .character import Character
from .event import Event
from .item import Item
from .location import Location
from .overall import Overall

class UserInteraction:
    """Keep a record of recent user interactions with the game
    so they can be displayed on the overview screen.
    """
    def __init__(self, user_id):
        self.game_token = g.game_token
        self.user_id = user_id
        self.char = None
        self.action_obj = None  # object of most recent interaction
        self.action_type = None  # class such as Item or Location

    @classmethod
    def get_collection(cls):
        return g.db['user_interactions']

    def to_json(self):
        return {
            'game_token': self.game_token,
            'user_id': self.user_id,
            'timestamp': datetime.now(),
            'char_id': self.char.id if self.char else -1,
            'action_id': self.action_obj.id if self.action_obj else -1,
            'action_type': self.action_type.__name__ if self.action_type else None
        }

    @classmethod
    def from_json(cls, data):
        instance = cls(data['user_id'])
        instance.game_token = data['game_token']
        instance.timestamp = data['timestamp']
        char_id = int(data['char_id'])
        if char_id >= 0: 
            instance.char = Character.get_by_id(char_id)
        action_id = int(data['action_id'])
        instance.action_type = globals().get(data['action_type'], None)
        if action_id >= 0 and instance.action_type:
            instance.action_obj = instance.action_type.get_by_id(action_id)
        return instance

    def to_db(self):
        doc = self.to_json()
        query = {
            'game_token': self.game_token,
            'user_id': self.user_id,
            'char_id': self.char.id if self.char else -1}
        collection = self.__class__.get_collection()
        if collection.find_one(query):
            print(f"Updating doc for {self.__class__.__name__} with user_id {self.user_id}")
            collection.replace_one(query, doc)
        else:
            print(f"Inserting doc for {self.__class__.__name__} with user_id {self.user_id}")
            collection.insert_one(doc)

    def action_name(self):
        return self.action_obj.name if self.action_obj else ""

    def action_link(self):
        return (
            f"/play/{self.action_type.name}/{self.action_obj.id}"
            if self.action_obj else "")

    @classmethod
    def recent_interactions(cls, threshold_minutes=2):
        threshold_time = datetime.now() - timedelta(minutes=threshold_minutes)
        pipeline = [{
                '$match': {
                    'game_token': g.game_token,
                    'timestamp': {'$gt': threshold_time}}
            }, {
                '$group': {
                    '_id': {'user_id': '$user_id', 'char_id': '$char_id'},
                    'max_timestamp': {'$max': '$timestamp'},
                    'interaction': {'$first': '$$ROOT'}}
            }, {
                '$replaceRoot': {'newRoot': '$interaction'}
            }
        ]
        entries = cls.get_collection().aggregate(pipeline)
        return [cls.from_json(entry) for entry in entries]


