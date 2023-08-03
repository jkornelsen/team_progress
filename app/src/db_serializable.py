from flask import g
from types import SimpleNamespace
from psycopg2.extras import RealDictCursor, execute_values
from database import column_counts, pretty

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

def new_game_data():
    from src.game_data import GameData
    return GameData()

def load_game_data():
    from src.game_data import GameData
    return GameData.from_db()

class MutableNamespace(SimpleNamespace):
    def __setattr__(self, key, value):
        """Allow setting attributes dynamically"""
        object.__setattr__(self, key, value)

    def setdefault(self, key, default):
        """Simulate the setdefault() behavior for a MutableNamespace object"""
        if not hasattr(self, key):
            setattr(self, key, default)
        return getattr(self, key)

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
        print(pretty(query, values))
        result = None
        with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, tuple(values))
            if fetch:
                result = MutableNamespace(**cursor.fetchone())
        if commit:
            g.db.commit()
        return result

    @classmethod
    def insert_multiple(cls, table, column_names, values, commit=True):
        if not values:
            return
        sql = f"""
            INSERT INTO {table} ({column_names})
            VALUES %s
        """
        print(pretty(sql, values))
        with g.db.cursor() as cursor:
            execute_values(cursor, sql, values, template=None, page_size=100)
        if commit:
            g.db.commit()

    @classmethod
    def execute_select(cls, query, values=None, fetch_all=True):
        """Returns data as a list of objects with attributes that are
        column names.
        For example, to get the name column of the first row: result[0].name
        """
        print(pretty(query, values))
        with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, values)
            if fetch_all:
                result = [MutableNamespace(**row) for row in cursor.fetchall()]
            else:
                result = MutableNamespace(**cursor.fetchone())
            return result

    @classmethod
    def select_tables(cls, query_without_tables, values, tables, fetch_all=True):
        """Query to grab all values for more than one table and separate
        results by table.
        Returns a list of rows arranged by table, for example:
            [item_data, progress_data]
        """
        query = query_without_tables.format(tables=tables)
        print(pretty(query, values))
        results = []
        with g.db.cursor() as cursor:
            cursor.execute(query, values)
            if fetch_all:
                rows = cursor.fetchall()
            else:
                rows = [cursor.fetchone()]
            column_names = [desc[0] for desc in cursor.description]
            table_column_indices = {}
            current_column = 0
            for table in tables:
                num_cols = column_counts(table)
                table_column_indices[table] = (current_column, current_column + num_cols)
                current_column += num_cols
            for row in rows:
                result = []
                for table in tables:
                    start_idx, end_idx = table_column_indices[table]
                    table_data = MutableNamespace(**dict(zip(
                        column_names[start_idx:end_idx],
                        row[start_idx:end_idx])))
                    result.append(table_data)
                results.append(result)
        if fetch_all:
            return results
        else:
            return results[0]

class Identifiable(DbSerializable):
    __abstract__ = True

    def __init__(self, new_id=0):
        super().__init__()
        if new_id == 'new' or new_id == '':
            self.id = 0
        else:
            self.id = int(new_id)

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
        NONSCALAR_TYPES = (dict, list, tuple, set)
        fields = [field for field in doc.keys()
            if not isinstance(doc[field], NONSCALAR_TYPES)]
        if not doc.get('id'):
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
    def from_json(cls, json_data):
        raise NotImplementedError()

    @classmethod
    def list_from_json(cls, json_data):
        print(f"{cls.__name__}.list_from_json()")
        instances = []
        for entity_data in json_data:
            instances.append(
                cls.from_json(entity_data))
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
    def list_from_db(cls):
        print(f"{cls.__name__}.list_from_db()")
        data = DbSerializable.execute_select(f"""
            SELECT *
            FROM {cls.tablename()}
            WHERE game_token = %s
        """, (g.game_token,))
        instances = [cls.from_json(vars(dat)) for dat in data]
        return instances

    @classmethod
    def from_db(cls, id_to_get):
        print(f"{cls.__name__}.from_db()")
        data = DbSerializable.execute_select(f"""
            SELECT *
            FROM {cls.tablename()}
            WHERE game_token = %s
                AND id = %s
        """, (g.game_token, id_to_get), fetch_all=False)
        instance = cls.from_json(vars(data))
        return instance

