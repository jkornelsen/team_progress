from datetime import datetime, timedelta
import logging

from flask import g, request, session, url_for

from .db_serializable import DbSerializable, coldef

logger = logging.getLogger(__name__)
tables_to_create = {
    'user_interactions': f"""
        {coldef('game_token')} NOT NULL,
        username varchar(50) NOT NULL,
        timestamp timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
        route varchar(50) NOT NULL,
        entity_id varchar(20),
        UNIQUE (game_token, username, route, entity_id)
        """,
    'game_messages': f"""
        {coldef('game_token')},
        timestamp timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
        message text,
        count integer
        """,
    }

def determine_entity_type(endpoint):
    """Determine the entity type based on the endpoint.
    For example, if endpoint is '/configure/item/<item_id>', return 'items'
    """
    if endpoint.startswith('/configure/') or endpoint.startswith('/play/'):
        entity_name = endpoint.split('/')[2]  # Get the part after the match
        entity_name = entity_name.capitalize()
        try:
            entity_class = globals()[entity_name]
            if issubclass(entity_class, DbSerializable):
                return entity_class
        except KeyError:
            pass
    return None

class UserInteraction(DbSerializable):
    """Keep a record of recent user interactions with the game
    so they can be displayed on the overview screen.
    """
    def __init__(self, username):
        super().__init__()
        self.username = username
        self.timestamp = datetime.min
        self.route_endpoint = ""  # for example "configure_item"
        self.entity_id = ""
        self.entity_name = ""

    @classmethod
    def tablename(cls):
        return 'user_interactions'

    def _base_export_data(self):
        return {
            'username': self.username,
            'timestamp': datetime.now(),
            'route': self.route_endpoint,
            'entity_id': self.entity_id,
            }

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = cls(data['username'])
        instance.timestamp = data.get('timestamp', datetime.min)
        instance.route_endpoint = data.get('route', "")
        instance.entity_id = data.get('entity_id', "")
        if instance.entity_id and instance.entity_id != "new":
            entity_cls = determine_entity_type(instance.route_endpoint)
            if entity_cls:
                entity_obj = entity_cls.get_by_id(instance.entity_id)
                instance.entity_name = entity_obj.name
        return instance

    def to_db(self):
        doc = self.dict_for_main_table()
        doc['game_token'] = g.game_token
        fields = list(doc.keys())
        placeholders = ','.join(['%s'] * len(fields))
        update_fields = [field for field in fields
            if field not in ('game_token', 'username', 'route', 'entity_id')]
        update_placeholders = ', '.join(
            [f"{field}=%s" for field in update_fields])
        query = f"""
            INSERT INTO {{table}} ({', '.join(fields)})
            VALUES ({placeholders})
            ON CONFLICT (game_token, username, route, entity_id) DO UPDATE
            SET {update_placeholders}
            """
        values = [doc[field] for field in fields]
        update_values = [doc[field] for field in update_fields]
        self.execute_change(query, values + update_values)

    def action_display(self):
        def format_name(name):
            words = name.split('_')
            words = [word.capitalize() for word in words]
            return ' '.join(words)
        display_parts = [format_name(self.route_endpoint)]
        if self.entity_name:
            entity_display = self.entity_name
            MAX_NAME_LEN = 100
            if len(entity_display) > MAX_NAME_LEN:
                entity_display = f"{entity_display[:MAX_NAME_LEN]}..."
            display_parts.append(entity_display)
        return " ".join(display_parts)

    def action_link(self):
        from app import get_parameter_name  # avoid circular import
        parameter_name = get_parameter_name(self.route_endpoint)
        kwargs = {parameter_name: self.entity_id
            } if parameter_name and self.entity_id else {}
        return url_for(self.route_endpoint, **kwargs)

    @classmethod
    def log_visit(cls, username):
        """Make sure the user is listed in the db as recently connected,
        and keep track of the route so that users can click to return
        there."""
        interaction = cls(username)
        interaction.route_endpoint = request.endpoint
        if not interaction.route_endpoint:
            logger.debug("No endpoint of request object")
            interaction.route_endpoint = "/"
        from app import get_parameter_name  # avoid circular import
        parameter_name = get_parameter_name(interaction.route_endpoint)
        view_args = request.view_args
        if view_args:
            entity_id = view_args.get(parameter_name)
            if entity_id is not None:
                interaction.entity_id = entity_id
        else:
            logger.debug("No view_args for %s", interaction.route_endpoint)
        interaction.to_db()

    @classmethod
    def recent_interactions(cls, threshold_minutes=2):
        g.game_data.entity_names_from_db()
        threshold_time = datetime.now() - timedelta(minutes=threshold_minutes)
        query = f"""
            SELECT DISTINCT ON (game_token, username)
                username, route, entity_id, timestamp
            FROM {cls.tablename()}
            WHERE game_token = %s
                AND timestamp > %s
            ORDER BY game_token, username, timestamp DESC
            """
        values = (g.game_token, threshold_time,)
        rows = cls.execute_select(query, values)
        # Don't include usernames which have later been changed
        interactions = [
            UserInteraction.from_data(vars(row))
            for row in rows
            if row.route != 'change_user']
        return interactions

class MessageLog(DbSerializable):
    @classmethod
    def tablename(cls):
        return 'game_messages'

    @classmethod
    def add(cls, message):
        result = cls.execute_select(""" 
            SELECT count
            FROM {table}
            WHERE game_token = %s AND message = %s
            AND timestamp IN (
                SELECT timestamp
                FROM {table}
                WHERE game_token = %s
                ORDER BY timestamp DESC
                LIMIT 7)
            """, (g.game_token, message, g.game_token), fetch_all=False)
        if result:
            # Combine with other recent identical messages
            count = result.count + 1
            cls.execute_change("""
                UPDATE {table}
                SET count = %s
                WHERE game_token = %s AND message = %s
                """, (count, g.game_token, message))
        else:
            cls.execute_change("""
                INSERT INTO {table} (game_token, message, count)
                VALUES (%s, %s, %s)
                """, (g.game_token, message, 1))
        counter_var = 'log_trim_count'
        if counter_var not in session:
            session[counter_var] = 0
        session[counter_var] += 1
        if session[counter_var] >= 20:
            cls.trim()
            session[counter_var] = 0
        return message

    @classmethod
    def get_recent(cls):
        """Get the most recent messages, oldest at the top."""
        rows = cls.execute_select("""
            SELECT message, count
            FROM {table}
            WHERE game_token = %s
            ORDER BY timestamp DESC
            LIMIT 50
            """, (g.game_token,))
        return rows[::-1]

    @classmethod
    def clear(cls):
        cls.execute_change("""
            DELETE FROM {table}
            WHERE game_token = %s
            """, (g.game_token,))

    @classmethod
    def trim(cls):
        cls.execute_change("""
            DELETE FROM {table}
            WHERE game_token = %s
            AND timestamp < (
                SELECT MIN(timestamp) FROM (
                    SELECT timestamp
                    FROM {table}
                    WHERE game_token = %s
                    ORDER BY timestamp DESC
                    LIMIT 50
                ) AS recent
            )
            """, (g.game_token, g.game_token))
