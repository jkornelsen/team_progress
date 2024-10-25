import logging
from types import SimpleNamespace

from flask import g
from psycopg2.extras import RealDictCursor, execute_values
import psycopg2

from database import column_counts, pretty
from .utils import NumTup, caller_info

logger = logging.getLogger(__name__)
#logger.setLevel(logging.INFO)  # don't log all SQL

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
            name varchar(255) not null,
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
        logger.debug("%s\n%s", caller_info(), pretty(query, values))
        with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, values)
            if fetch_all:
                result = [MutableNamespace(**row) for row in cursor.fetchall()]
                logger.debug("got %d rows", len(result))
            else:
                result = MutableNamespace(**(cursor.fetchone() or {}))
                if result:
                    logger.debug("got a result")
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
        logger.debug("%s\n%s", caller_info(), pretty(query, values))
        results = []
        with g.db.cursor() as cursor:
            cursor.execute(query, values)
            if fetch_all:
                rows = cursor.fetchall()
                logger.debug("got %d rows", len(rows))
            else:
                row = cursor.fetchone()
                rows = [row] if row else []
                if row:
                    logger.debug("got a result")
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
        logger.debug("%s\n%s", caller_info(), pretty(query, values))
        result = None
        try:
            with g.db.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, tuple(values))
                if fetch:
                    result = MutableNamespace(**cursor.fetchone())
                    if result:
                        logger.debug("got a result")
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
        logger.debug("%s\n%s", caller_info(), pretty(sql, values))
        with g.db.cursor() as cursor:
            execute_values(cursor, sql, values, template=None, page_size=100)
            logger.debug("Inserted %d rows.", cursor.rowcount)

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
        self.id = self.single_id([new_id])

    @classmethod
    def single_id(cls, ids):
        """Return the single id from a list as int, or else 0."""
        if not ids or cls.empty_values(ids):
            return 0
        if len(ids) == 1:
            id_ = ids[0]
            return int(id_)
        return 0

    @staticmethod
    def empty_values(ids):
        """Returns True if the list has elements but consists only of empty
        values.
        """
        if not ids:
            return False
        if all(not id_ or id_ in ['new', '0']
                for id_ in ids):
            return True
        return False

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
    def listname(cls):
        """Attributes of GameData and ActiveData for each entity.
        Same as table name.
        """
        return cls.tablename()

    @classmethod
    def typename(cls):
        """Short string to refer to an entity class."""
        return cls.basename()

    @classmethod
    def id_field(cls):
        """String to refer to an entity id outside of its base table."""
        return f'{cls.typename()}_id'

    @classmethod
    def readable_type(cls):
        """Used to display error messages."""
        return cls.tablename().capitalize()

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
        old_id = self.id
        self.id = row.id
        if self.id != old_id:
            logger.info("updated id from %s to %s", old_id, self.id)

    def remove_from_db(self):
        self.execute_change("""
            DELETE FROM {table}
            WHERE game_token = %s AND id = %s
            """, (g.game_token, self.id))
        entity_list = self.get_list()
        if self in entity_list:
            entity_list.remove(self)

class CompleteIdentifiable(Identifiable):
    """Classes such as Item that can write to DB alone.
    Top level in JSON.
    """
    __abstract__ = True

    @classmethod
    def load_complete_objects(cls, ids=None):
        """Load objects from db with everything needed for storing
        to db or JSON file.
        :param ids: only load the specified objects
        """
        raise NotImplementedError()

    @classmethod
    def load_complete_object(cls, id_to_get):
        return next(iter(cls.load_complete_objects([id_to_get])), None)

class DependentIdentifiable(Identifiable):
    """Classes such as Recipe that may be able to write to DB
    but only within the context of another class.
    Not top level in JSON.
    """
    __abstract__ = True

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

    def add_limit_in(self, fields, values, conjunction="IN"):
        """Add a limiting condition for multiple fields, such as IDs."""
        if values:
            values = [v for v in values if v]
        if values:
            placeholders = ', '.join(['%s'] * len(values))
            condition = f"{conjunction} ({placeholders})"
            if isinstance(fields, str):
                self.query += f" AND {fields} {condition}"
                self.values.extend(values)
            else:
                fields_condition = ' OR '.join(
                    [f"{field} {condition}" for field in fields])
                self.query += f" AND ({fields_condition})"
                self.values.extend(values * len(fields))

    def add_limit_expr(self, expr, values):
        """Add a freeform limiting expression."""
        if values and values[0]:
            self.query += f" AND {expr}"
            self.values.extend(values)

    def sort_by(self, field):
        self.query += f"\nORDER BY {field}"

def precision(numstr, places):
    """Convert string to float with specified number of decimal places."""
    num = float(numstr)
    truncated_str = f'{num:.{places}f}'
    return float(truncated_str)
