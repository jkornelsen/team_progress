from flask import g
from types import SimpleNamespace
from psycopg2.extras import RealDictCursor

def coldef(which):
    """Definitions for commonly used columns for creating a table."""
    if which == 'game_token':
        return "game_token VARCHAR(50) NOT NULL"
    elif which == 'id':
        # include game token as well
        return f"""{coldef('game_token')},
        id SERIAL,
        PRIMARY KEY (game_token, id)"""
    elif which == 'name':
        return "name VARCHAR(255) NOT NULL"
    elif which == 'description':
        return "description TEXT"
    elif which == 'toplevel':
        return "toplevel BOOLEAN NOT NULL"
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

class DbSerializable():
    """Parent class with methods for serializing to database along with some
    other things that entities have in common.
    """
    __abstract__ = True

    def __init__(self):
        self.game_token = g.game_token

    @classmethod
    def get_table(cls):
        return "{}s".format(cls.__name__.lower())

    @classmethod
    def execute_change(cls, query_without_table, values,
            commit=True, fetch=False):
        """Returning a value is useful when inserting
        auto-generated IDs.
        """
        query = query_without_table.format(table=cls.get_table())
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

    instances = []  # all objects of this class

    def __init__(self, id):
        super().__init__()
        self.id = id

    @classmethod
    def get_by_id(cls, id_to_get):
        id_to_get = int(id_to_get)
        return next(
            (instance for instance in cls.instances
            if instance.id == id_to_get), None)

    def to_db(self):
        doc = self.to_json()
        doc['game_token'] = g.game_token
        fields = list(doc.keys())
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

    @classmethod
    def list_from_json(cls, json_data, id_references=None):
        print(f"{cls.__name__}.list_from_json()")
        cls.instances.clear()
        for entity_data in json_data:
            cls.from_json(entity_data, id_references)
        cls.last_id = max(
            (instance.id for instance in cls.instances), default=0)
        return cls.instances

    @classmethod
    def list_to_db(cls):
        print(f"{cls.__name__}.list_to_db()")
        table = cls.get_table()
        existing_ids = set(
            str(doc['id'])
            for doc in table.find({'game_token': g.game_token}))
        for instance in cls.instances:
            instance.to_db()
        for doc_id in existing_ids:
            if doc_id not in (str(instance.id) for instance in cls.instances):
                print(f"Removing document with id {doc_id}")
                cls.remove_from_db(doc_id)

    @classmethod
    def list_from_db(cls, id_references=None):
        print(f"{cls.__name__}.list_from_db()")
        cls.instances.clear()
        table = cls.get_table()
        data = DbSerializable.execute_select(f"""
            SELECT *
            FROM {table}
            WHERE game_token = %s
        """, (g.game_token,))
        instances = [cls.from_json(vars(dat), id_references) for dat in data]
        cls.last_id = max(
            (instance.id for instance in cls.instances), default=0)
        return instances

