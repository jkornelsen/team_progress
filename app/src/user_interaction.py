from datetime import datetime, timedelta
from flask import g

from .db_serializable import Identifiable, coldef

from .attrib import Attrib
from .character import Character
from .event import Event
from .item import Item
from .location import Location
from .overall import Overall

tables_to_create = {
    'user_interactions': f"""
        game_token VARCHAR(50) NOT NULL,
        username VARCHAR(50),
        timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        char_id INTEGER,
        action_id INTEGER,
        action_type VARCHAR(50),
        UNIQUE (game_token, username, char_id, action_id, action_type) ON CONFLICT REPLACE
    """
}

class UserInteraction(DbSerializable):
    """Keep a record of recent user interactions with the game
    so they can be displayed on the overview screen.
    """
    def __init__(self, username):
        self.game_token = g.game_token
        self.username = username
        self.timestamp = datetime.min
        self.char = None
        self.action_obj = None  # object of most recent interaction
        self.action_type = None  # class such as Item or Location

    @classmethod
    def get_table(cls):
        return 'user_interactions'

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
        doc = self.to_json()
        doc['game_token'] = g.game_token
        fields = list(doc.keys())
        values = [doc[field] for field in fields]
        TABLE = "{table}"  # partial string format
        update_fields = [field for field in fields
            if field not in ('game_token', 'username', 'char_id')]
        field_exprs = ', '.join([f"{field}=%s" for field in update_fields])
        update_values = (
            [doc[field] for field in update_fields]
            + [doc['game_token'], doc['username'], doc['char_id']])
        query = f"""
            UPDATE {TABLE}
            SET {field_exprs}
            WHERE game_token = %s AND username = %s AND char_id = %s
        """
        try:
            self.execute_change(query, values)
            return
        except psycopg2.IntegrityError:
            # no record with those values exists yet
            pass
        placeholders = ','.join(['%s'] * len(fields))
        query = f"""
            INSERT INTO {TABLE} ({', '.join(fields)})
            VALUES ({placeholders})
        """
        self.execute_change(query, values)

    def action_name(self):
        return self.action_obj.name if self.action_obj else ""

    def action_link(self):
        return (
            f"/play/{self.action_type.name}/{self.action_obj.id}"
            if self.action_obj else "")

    @classmethod
    def recent_interactions(cls, threshold_minutes=2):
        threshold_time = datetime.now() - timedelta(minutes=threshold_minutes)
        query = f"""
            SELECT DISTINCT ON (username, char_id)
                username, char_id, timestamp, action_id, action_type
            FROM {cls.get_table()}
            WHERE timestamp > %s
            ORDER BY username, char_id, timestamp DESC
        """
        values = (threshold_time,)
        return cls.execute_select(query, values, fetch_all=True)

