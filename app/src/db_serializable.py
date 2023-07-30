from flask import g
from types import SimpleNamespace
from psycopg2.extras import RealDictCursor

def coldef(which):
    """Definitions for commonly used columns for creating a table."""
    if which == 'game_token':
        return "game_token varchar(50) NOT NULL"
    elif which == 'id':
        # include game token as well
        return f"""id SERIAL,
        {coldef('game_token')},
        PRIMARY KEY (id, game_token)"""
    elif which == 'name':
        return "name varchar(255) NOT NULL"
    elif which == 'description':
        return "description text"
    elif which == 'toplevel':
        return "toplevel boolean NOT NULL"
    else:
        raise Exception(f"Unexpected coldef type '{which}'")

def pretty(text):
    """Pretty-print SQL by indenting consistently and removing extra
    newlines."""
    text = text.strip('\r\n')
    lines = text.split('\n')
    indented_lines = [' ' * 8 + line.strip() for line in lines]
    indented_text = '\n'.join(indented_lines)
    return indented_text

def load_game_data():
    from src.game_data import GameData
    GameData.from_db()

class DbSerializable():
    """Parent class with methods for serializing to database along with some
    other things that entities have in common.
    """
    __abstract__ = True

    def __init__(self):
        self.game_token = g.game_token
        self.game_data = None

    @classmethod
    def tablename(cls):
        return "{}s".format(cls.__name__.lower())

    @classmethod
    def execute_change(cls, query_without_table, values,
            commit=True, fetch=False):
        """Returning a value is useful when inserting
        auto-generated IDs.
        """
        query = query_without_table.format(table=cls.tablename())
        result = None
        print(pretty(query), values)
        with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, tuple(values))
            if fetch:
                result = SimpleNamespace(**cursor.fetchone())
        if commit:
            g.db.commit()
        return result

    @classmethod
    def execute_select(cls, query, values=None, fetch_all=True):
        """Returns data as a list of objects with attributes that are
        column names.
        For example, to get the name column of the first row: result[0].name
        """
        print(pretty(query), values)
        with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, values)
            if fetch_all:
                result = [SimpleNamespace(**row) for row in cursor.fetchall()]
            else:
                result = SimpleNamespace(**cursor.fetchone())
            return result

class Identifiable(DbSerializable):
    __abstract__ = True

    def __init__(self, id):
        super().__init__()
        self.id = id

    @classmethod
    def get_by_id(cls, id_to_get):
        id_to_get = int(id_to_get)
        entity_list = g.game_data.get_list(cls)
        return next(
            (instance for instance in entity_list
            if instance.id == id_to_get), None)

    @classmethod
    def listname(cls):
        """Attributes of GameData for each entity. Same as table name."""
        return cls.tablename()

    @classmethod
    def get_list(cls):
        if 'game_data' in g:
            return g.game_data.get_list(cls)
        return []

    def to_db(self):
        self.json_to_db(
            self.to_json())

    def json_to_db(self, doc):
        doc['game_token'] = g.game_token
        fields = list(doc.keys())
        fields = [field for field in doc.keys()
            if not isinstance(doc[field], dict]
        if doc.get('id') in ('auto', ''):
            # Remove the 'id' field from the fields to be inserted
            fields = [field for field in fields if field != 'id']
        placeholders = ','.join(['%s'] * len(fields))
        update_fields = [
            field for field in fields
            if field not in ('id', 'game_token')]
        update_placeholders = ', '.join(
            [f"{field}=%s" for field in update_fields])
        query = f"""
            INSERT INTO {{table}} ({', '.join(fields)})
            VALUES ({placeholders})
            ON CONFLICT (id, game_token) DO UPDATE
            SET {update_placeholders}
            RETURNING id
        """
        values = [doc[field] for field in fields]
        update_values = [doc[field] for field in update_fields]
        row = self.execute_change(query, values + update_values, fetch=True)
        self.id = row.id

    def remove_from_db(self):
        self.execute_change(
            """
                DELETE FROM {table}
                WHERE id = %s AND game_token = %s
            """,
            (self.id, self.game_token))
        entity_list = self.get_list()
        if self in entity_list:
            entity_list.remove(self)

    @classmethod
    def list_from_json(cls, json_data, id_references=None):
        print(f"{cls.__name__}.list_from_json()")
        instances = []
        for entity_data in json_data:
            instances.append(
                cls.from_json(entity_data, id_references))
        return instances

    @classmethod
    def list_to_db(cls):
        print(f"{cls.__name__}.list_to_db()")
        table = cls.tablename()
        existing_ids = set(
            str(doc['id'])
            for doc in table.find({'game_token': g.game_token}))
        entity_list = g.game_data.get_list(cls)
        for instance in entity_list:
            instance.to_db()
        for doc_id in existing_ids:
            if doc_id not in (str(instance.id) for instance in entity_list):
                print(f"Removing document with id {doc_id}")
                cls.remove_from_db(doc_id)

    @classmethod
    def list_from_db(cls, id_references=None):
        print(f"{cls.__name__}.list_from_db()")
        table = cls.tablename()
        data = DbSerializable.execute_select(f"""
            SELECT *
            FROM {table}
            WHERE game_token = %s
        """, (g.game_token,))
        instances = [cls.from_json(vars(dat), id_references) for dat in data]
        return instances

