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

class DbSerializable():
    """Parent class with methods for serializing to database along with some
    other things that entities have in common.
    """
    __abstract__ = True

    def __init__(self):
        self.game_token = g.game_token

    @classmethod
    def get_table(cls):
        return cls.__name__.lower()

    @classmethod
    def execute_change(cls, query_without_table, values,
            commit=True, fetch=False):
        query = query_without_table.format(table=cls.get_table())
        result = None
        with g.db.cursor() as cursor:
            cursor.execute(query, tuple(values))
            if fetch:
                result = cursor.fetchone()
        if commit:
            g.db.commit()
        return result

    @classmethod
    def execute_select(cls, query, values=None, fetch_all=True):
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
        values = [doc[field] for field in fields]
        if doc.get('id') not in ('auto', ''):
            update_fields = [field for field in fields
                if field not in ('id', 'game_token')]
            field_exprs = ', '.join([f"{field}=%s" for field in update_fields])
            update_values = (
                [doc[field] for field in update_fields]
                + [doc['id'], doc['game_token']])
            query = f"""
                UPDATE {{table}}
                SET {field_exprs}
                WHERE id = %s AND game_token = %s
            """
            try:
                self.execute_change(query, values)
                return
            except psycopg2.IntegrityError:
                # id doesn't exist yet
                pass
        placeholders = ','.join(['%s'] * len(fields))
        query = f"""
            INSERT INTO {{table}} ({', '.join(fields)})
            VALUES ({placeholders})
            RETURNING id
        """
        row = self.execute_change(query, values, fetch=True)
        self.id = row[0]

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
        collection = cls.get_table()
        existing_ids = set(
            str(doc['id'])
            for doc in collection.find({'game_token': g.game_token}))
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
        collection = cls.get_table()
        docs = collection.find({'game_token': g.game_token})
        instances = [cls.from_json(doc, id_references) for doc in docs]
        cls.last_id = max(
            (instance.id for instance in cls.instances), default=0)
        return instances

