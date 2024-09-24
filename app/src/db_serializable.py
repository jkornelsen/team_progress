import logging
from types import SimpleNamespace

from flask import g
from psycopg2.extras import RealDictCursor, execute_values
import psycopg2

from database import column_counts, pretty
from .utils import NumTup

logger = logging.getLogger(__name__)
logger.setLevel(logging.CRITICAL)  # turn off for this module

def coldef(which):
    """Definitions for commonly used columns for creating a table."""
    if which == 'game_token':
        return "game_token varchar(50)"
    if which == 'id':
        return f"""id integer,
        {coldef('game_token')},
        PRIMARY KEY (id, game_token)"""
    if which == 'name':
        return f"""{coldef('id')},
            name varchar(255) NOT NULL,
            description text"""
    raise ValueError(f"Unexpected coldef type '{which}'")

def numtups_to_lists(values):
    """Convert NumTup objects to lists for database insertion."""
    return [
        val.as_list() if isinstance(val, NumTup) else val
        for val in values
        ]

class MutableNamespace(SimpleNamespace):
    def __setattr__(self, key, value):
        """Allow setting attributes dynamically"""
        object.__setattr__(self, key, value)

    def __bool__(self):
        return len(self.__dict__) > 0

    def setdefault(self, key, default):
        """Get the value of an attribute after setting default if needed."""
        if not hasattr(self, key):
            setattr(self, key, default)
        return getattr(self, key)

    def get(self, key, default=None):
        """Get the value of an attribute or return default."""
        return getattr(self, key, default)

class Serializable():
    """Abstract class for exporting and importing data."""
    def _base_export_data(self):
        """Fields that get exported to both JSON and database."""
        raise NotImplementedError()

    def dict_for_json(self):
        """Fields for exporting to JSON file."""
        return self._base_export_data()

    def dict_for_main_table(self):
        """Fields for writing to the main database table of this class."""
        return self._base_export_data()

    @classmethod
    def prepare_dict(cls, data):
        """Make sure data is passed as a dict before we use it
        infrom_data(). It could be a MutableNamespace beforehand.
        """
        if not isinstance(data, dict):
            data = vars(data)
        return data

    @classmethod
    def from_data(cls, data):
        """Create instance of this class from the given data.
        Data is from a json file or database,
        either a dict or a record-like object with attributes.
        """
        raise NotImplementedError()

#pylint: disable=abstract-method
class DbSerializable(Serializable):
    """Abstract class for methods for serializing to database."""
    __abstract__ = True

    def __init__(self):
        super().__init__()
        self.game_token = g.game_token
        self.game_data = None

    @classmethod
    def basename(cls):
        return cls.__name__.lower()

    @classmethod
    def tablename(cls):
        return "{}s".format(cls.basename())

    @classmethod
    def execute_select(
            cls, query_without_tables="", values=None, tables=None,
            qhelper=None, fetch_all=True):
        """Returns data as a list of objects with attributes that are
        column names.
        For example, to get the name column of the first row: result[0].name
        """
        if qhelper:
            query_without_tables = qhelper.query
            values = qhelper.values
        if tables:
            query = query_without_tables.format(tables=tables)
        else:
            query = query_without_tables.format(table=cls.tablename())
        logger.debug(pretty(query, values))
        with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, values)
            if fetch_all:
                result = [MutableNamespace(**row) for row in cursor.fetchall()]
            else:
                result = MutableNamespace(**(cursor.fetchone() or {}))
            return result

    @classmethod
    def select_tables(
            cls, query_without_tables="", values=None, tables=None,
            qhelper=None, fetch_all=True):
        """Query to grab all values for more than one table and separate
        results by table.
        Returns a list of rows arranged by table, for example:
            [item_data, progress_data]
        """
        if qhelper:
            query_without_tables = qhelper.query
            values = qhelper.values
        query = query_without_tables.format(tables=tables)
        logger.debug(pretty(query, values))
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
        if fetch_all or not results:
            return results
        return results[0]

    @classmethod
    def execute_change(cls, query_without_table, values=None, fetch=False):
        """Returning a value is useful when inserting
        auto-generated IDs.
        """
        if values is None:
            values = []
        query = query_without_table.format(table=cls.tablename())
        logger.debug(pretty(query, values))
        result = None
        try:
            with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, tuple(values))
                if fetch:
                    result = MutableNamespace(**cursor.fetchone())
        except (psycopg2.OperationalError, psycopg2.ProgrammingError,
                psycopg2.IntegrityError, psycopg2.InterfaceError,
                psycopg2.InternalError) as e:
            raise DbError(str(e))
        return result

    @classmethod
    def insert_multiple(cls, table, column_names, values):
        if not values:
            return
        sql = f"""
            INSERT INTO {table} ({column_names})
            VALUES %s
            """
        logger.debug(pretty(sql, values))
        with g.db.cursor() as cursor:
            execute_values(cursor, sql, values, template=None, page_size=100)

    @classmethod
    def insert_single(cls, table, column_names, values):
        cls.insert_multiple(table, column_names, (values,))

    @classmethod
    def insert_multiple_from_data(cls, table, data):
        if len(data) == 0:
            return
        column_keys = data[0].keys()
        values = [
            tuple([g.game_token] + [req[key] for key in column_keys])
            for req in data]
        column_names = ", ".join(['game_token'] + list(column_keys))
        cls.insert_multiple(table, column_names, values)

    def to_db(self):
        """Write to the main table of this class."""
        data = self.dict_for_main_table()
        data['game_token'] = g.game_token
        fields = data.keys()
        placeholders = ','.join(['%s'] * len(fields))
        values = numtups_to_lists(
            [data[field] for field in fields])
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

#pylint: disable=abstract-method
class Identifiable(DbSerializable):
    __abstract__ = True

    """Abstract class for objects that have a unique id in the database."""
    def __init__(self, new_id=0):
        super().__init__()
        if new_id == 'new' or not new_id:
            self.id = 0
        else:
            self.id = int(new_id)

    @classmethod
    def get_by_id(cls, id_to_get):
        """Get from g.game_data list.
        To use g.active instead, do a basic dict lookup.
        """
        if not id_to_get:
            return None
        id_to_get = int(id_to_get)
        entity_list = g.game_data.get_list(cls)
        return next(
            (instance for instance in entity_list
            if instance.id == id_to_get), None)

    @classmethod
    @property
    def listname(cls):
        """Attributes of GameData and ActiveData for each entity.
        Same as table name.
        """
        return cls.tablename()

    @classmethod
    def get_list(cls):
        if 'game_data' in g:
            return g.game_data.get_list(cls)
        return []

    #pylint: disable=attribute-defined-outside-init
    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        return instance

    def to_db(self):
        data = self.dict_for_main_table()
        if not data:
            return
        data['game_token'] = g.game_token
        fields = data.keys()
        placeholders = ['%s'] * len(fields)
        values = numtups_to_lists(
            [data[field] for field in fields])
        if not data.get('id'):
            # Generate new id after max instead of specifying value
            id_index = list(fields).index('id')
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
    def load_complete_objects(cls, id_to_get=None):
        """Load objects from db with everything needed for storing
        to db or JSON file.
        :param id_to_get: specify to only load a single object
        """
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

class QueryHelper:
    """Build a query string and associated values."""
    def __init__(self, base_query, initial_values):
        self.query = base_query
        self.values = initial_values

    def add_limit(self, field, value):
        """Add a limiting condition, such as an ID."""
        if value:
            self.query += f" AND {field} = %s"
            self.values.append(value)

    def sort_by(self, field):
        self.query += f"\nORDER BY {field}"

def precision(numstr, places):
    """Convert string to float with specified number of decimal places."""
    num = float(numstr)
    truncated_str = f'{num:.{places}f}'
    return float(truncated_str)
