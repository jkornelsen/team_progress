from datetime import datetime, timedelta
from flask import g

from db import db
from .db_serializable import DbSerializable

from .attrib import Attrib
from .character import Character
from .event import Event
from .item import Item
from .location import Location
from .overall import Overall

class UserInteraction(DbSerializable):
    """Keep a record of recent user interactions with the game
    so they can be displayed on the overview screen.
    """
    __tablename__ = 'user_interactions'  # Define the table name

    username = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, onupdate=db.func.current_timestamp())
    char_id = db.Column(db.Integer, nullable=True)
    action_id = db.Column(db.Integer, nullable=True)
    action_type = db.Column(db.String(50), nullable=True)

    __table_args__ = (
        # Unique constraint for the nullable columns
        db.UniqueConstraint('game_token', 'id', name='user_interaction_pk'),
        db.UniqueConstraint('username', 'char_id', 'action_id', 'action_type',
                            name='user_interaction_unique_interaction')
    )
    char = db.relationship(
        'Character', backref='user_interactions', lazy=True,
        foreign_keys=[DbSerializable.game_token, char_id])

    def __init__(self, username):
        self.game_token = g.game_token
        self.username = username
        self.char = None
        self.action_obj = None  # object of most recent interaction
        self.action_type = None  # class such as Item or Location

    @classmethod
    def get_collection(cls):
        return g.db['user_interactions']

    def to_json(self):
        return {
            'game_token': self.game_token,
            'username': self.username,
            'timestamp': datetime.now(),
            'char_id': self.char.id if self.char else -1,
            'action_id': self.action_obj.id if self.action_obj else -1,
            'action_type': self.action_type.__name__ if self.action_type else None
        }

    @classmethod
    def from_json(cls, data):
        instance = cls(data['username'])
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
        query = {
            'game_token': self.game_token,
            'username': self.username,
            'char_id': self.char.id if self.char else -1
        }
        existing_interaction = UserInteraction.query.filter_by(**query).first()
        if existing_interaction:
            print(f"Updating for {self.__class__.__name__} with username {self.username}")
            for key, value in self.to_json().items():
                setattr(existing_interaction, key, value)
            db.session.commit()
        else:
            print(f"Inserting for {self.__class__.__name__} with username {self.username}")
            db.session.add(self)
            db.session.commit()

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
                    '_id': {'username': '$username', 'char_id': '$char_id'},
                    'max_timestamp': {'$max': '$timestamp'},
                    'interaction': {'$first': '$$ROOT'}}
            }, {
                '$replaceRoot': {'newRoot': '$interaction'}
            }
        ]
        entries = cls.get_collection().aggregate(pipeline)
        return [cls.from_json(entry) for entry in entries]

