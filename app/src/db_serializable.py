from flask import g
from types import SimpleNamespace
from psycopg2.extras import RealDictCursor, execute_values
from database import column_counts, pretty

def coldef(which):
    """Definitions for commonly used columns for creating a table."""
    if which == 'game_token':
        return "game_token varchar(50)"
    elif which == 'id':
        # include game token as well
        return f"""id integer,
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

def tuples_to_lists(values):
    """Prepare for insertion into db."""
    return [
        list(val) if isinstance(val, tuple)
        else val
        for val in values]

def tuple_to_pg_array(value):
    """Convert a tuple to a PostgreSQL array string,
    for example "{0, 0}".
    """
    if isinstance(value, tuple):
        return f"{{{', '.join(map(str, value))}}}"
    return value

def db_type_fields(doc):
    """Objects may have types such as dicts
    that aren't for inserting into the db.
    """
    #NON_DB_TYPES = (dict, list, tuple, set)
    NON_DB_TYPES = (dict, list, set)
    return [
        field for field in doc.keys()
        if not isinstance(doc[field], NON_DB_TYPES)]

class MutableNamespace(SimpleNamespace):
    def __setattr__(self, key, value):
        """Allow setting attributes dynamically"""
        object.__setattr__(self, key, value)

    def setdefault(self, key, default):
        """Get the value of an attribute after setting default if needed."""
        if not hasattr(self, key):
            setattr(self, key, default)
        return getattr(self, key)

    def get(self, key, default=None):
        """Get the value of an attribute or return default."""
        return getattr(self, key, default)

class DbSerializable():
    """Parent class with methods for serializing to database along with some
    other things that entities have in common.
    """
    __abstract__ = True

    def __init__(self):
        self.game_token = g.game_token
        self.game_data = None

    @classmethod
    def basename(cls):
        return cls.__name__.lower()

    @classmethod
    def tablename(cls):
        return "{}s".format(cls.basename())

    @classmethod
    def execute_select(cls, query_without_table, values=None, fetch_all=True):
        """Returns data as a list of objects with attributes that are
        column names.
        For example, to get the name column of the first row: result[0].name
        """
        query = query_without_table.format(table=cls.tablename())
        print(pretty(query, values))
        with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, values)
            if fetch_all:
                result = [MutableNamespace(**row) for row in cursor.fetchall()]
            else:
                result = MutableNamespace(**(cursor.fetchone() or {}))
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
                row = cursor.fetchone()
                rows = [row] if row else []
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

    @classmethod
    def execute_change(cls, query_without_table, values, fetch=False):
        """Returning a value is useful when inserting
        auto-generated IDs.
        """
        query = query_without_table.format(table=cls.tablename())
        print(pretty(query, values))
        result = None
        try:
            with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, tuple(values))
                if fetch:
                    result = MutableNamespace(**cursor.fetchone())
            if g.commit_db:
                g.db.commit()
        except (psycopg2.OperationalError, psycopg2.ProgrammingError,
                psycopg2.IntegrityError, psycopg2.InterfaceError,
                psycopg2.InternalError) as e:
            raise DbError(e)
        return result

    @classmethod
    def insert_multiple(cls, table, column_names, values):
        if not values:
            return
        sql = f"""
            INSERT INTO {table} ({column_names})
            VALUES %s
        """
        print(pretty(sql, values))
        with g.db.cursor() as cursor:
            execute_values(cursor, sql, values, template=None, page_size=100)
        if g.commit_db:
            g.db.commit()

    @classmethod
    def insert_single(cls, table, column_names, values):
        cls.insert_multiple(table, column_names, (values,))

    @classmethod
    def insert_multiple_from_dict(cls, table, data):
        if len(data) == 0:
            return
        column_keys = data[0].keys()
        values = [
            tuple([g.game_token] + [req[key] for key in column_keys])
            for req in data]
        column_names = ", ".join(['game_token'] + list(column_keys))
        cls.insert_multiple(table, column_names, values)

    def to_db(self):
        self.json_to_db(
            self.to_json())

    def json_to_db(self, doc):
        doc['game_token'] = g.game_token
        print("Debug - Document Content:")
        print(doc)
        fields = db_type_fields(doc)
        print("Debug - Fields:")
        print(fields)
        placeholders = ','.join(['%s'] * len(fields))
        print("Debug - Placeholders:")
        print(placeholders)
        values = tuples_to_lists([doc[field] for field in fields])
        print("Debug - Values:")
        print(values)
        update_fields = [
            field for field in fields
            if field not in ('game_token',)]
        update_placeholders = ', '.join(
            [f"{field}=EXCLUDED.{field}" for field in update_fields])
        query = f"""
            INSERT INTO {{table}} ({', '.join(fields)})
            VALUES ({placeholders})
            ON CONFLICT (game_token) DO UPDATE
            SET {update_placeholders}
        """
        self.execute_change(query, values)

    @classmethod
    def form_int(cls, request, field, default=0):
        """Get int from html form, handling empty strings."""
        val = request.form.get(field, default)
        try:
            val = int(val)
        except ValueError:
            val = 0
        return val

    @classmethod
    def form_dec(cls, request, field, default=0.0):
        """Get decimal number from html form, handling empty strings."""
        val = request.form.get(field, default)
        try:
            val = float(val)
        except ValueError:
            val = 0.0
        return val

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
        if not id_to_get:
            return None
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

    def json_to_db(self, doc):
        doc['game_token'] = g.game_token
        fields = db_type_fields(doc)
        placeholders = ['%s'] * len(fields)
        values = tuples_to_lists([doc[field] for field in fields])
        if not doc.get('id'):
            # Generate new id after max instead of specifying value
            id_index = fields.index('id')
            placeholders[id_index] = (
                "COALESCE((SELECT MAX(id) + 1 FROM {table}), 1)")
            values.pop(id_index)
        placeholders = ','.join(placeholders)
        update_fields = [
            field for field in fields
            if field not in ('game_token', 'id')]
        update_placeholders = ', '.join(
            [f"{field}=EXCLUDED.{field}" for field in update_fields])
        query = f"""
            INSERT INTO {{table}} ({', '.join(fields)})
            VALUES ({placeholders})
            ON CONFLICT (game_token, id) DO UPDATE
            SET {update_placeholders}
            RETURNING id
        """
        row = self.execute_change(query, values, fetch=True)
        self.id = row.id

    def remove_from_db(self):
        self.execute_change("""
            DELETE FROM {table}
            WHERE game_token = %s AND id = %s
        """, (self.game_token, self.id))
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
                cls(doc_id).remove_from_db()

    @classmethod
    def list_from_db(cls):
        return cls.data_for_file()

    @classmethod
    def data_for_file(cls):
        raise NotImplementedError()

class DbError(Exception):
    """Custom exception for database-related errors."""
    def __init__(self, original_exception):
        super().__init__(str(original_exception))
        self.original_exception = original_exception

class DeletionError(DbError):
    """Exception raised when an error occurs during deletion."""
    def __init__(self, original_exception, item_id=None):
        self.item_id = item_id
        message = (f"Could not delete item (ID: {item_id})." if item_id
            else "Could not delete item.")
        super().__init__(message)
        self.original_exception = original_exception

def precision(numstr, places):
    """Convert string to float with specified number of decimal places."""
    num = float(numstr)
    truncated_str = f'{num:.{places}f}'
    return float(truncated_str)
