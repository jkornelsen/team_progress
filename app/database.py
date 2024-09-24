import logging
import psycopg2

from flask import Flask, g

logger = logging.getLogger(__name__)

try:
    from sensitive import DB_PASSWORD
except ImportError:
    # A default PostgreSQL installation uses 'trust' authentication,
    # which does not require a password.
    # This setup is suitable for use on a local LAN.
    # For external web deployments, update postgres_data/pg_hba.conf to use
    # password-based authentication and edit sensitive.py to set a real password.
    logger.warning("Not using a database password.")
    DB_PASSWORD = 'no password needed with trust'

def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(
            dbname='app',
            user='postgres',
            password=DB_PASSWORD,
            host='localhost',
            port='5432'
        )
    set_autocommit(True)
    return g.db

def close_db():
    db = g.pop('db', None)
    if db is not None:
        db.close()

def set_autocommit(commit):
    logger.debug("autocommit: %s to %s", g.db.autocommit, commit)
    g.db.autocommit = commit

def pretty(text, values=None):
    """Pretty-print SQL by indenting consistently and removing extra
    spaces and newlines."""
    text = text.strip()
    lines = text.split('\n')
    indent = ' ' * 8
    indented_lines = [indent + line.strip() for line in lines]
    indented_text = '\n'.join(indented_lines)
    if values:
        indented_text += f"\n{indent}values={values}"
    return indented_text

def create_all():
    import importlib
    module_names = [
        'progress',
        'attrib',
        'character',
        'event',
        'file',
        'item',
        'location',
        'overall',
        'recipe',
        'user_interaction',
        'db_relations'
    ]
    db = get_db()
    for module_name in module_names:
        module = importlib.import_module(f'src.{module_name}')
        for table, schema in module.tables_to_create.items():
            command = pretty(
                "CREATE TABLE {} (\n{})".format(
                table, pretty(schema)))
            logger.debug(command)
            with db.cursor() as cursor:
                cursor.execute(command)
    db.commit()

def column_counts(table_name):
    """Generate the values with:
        SELECT '''' || table_name || ''': ' || COUNT(column_name) || ','
        FROM information_schema.columns
        WHERE table_schema = 'public'
        GROUP BY table_name
        ORDER BY table_name;
    """
    lookup = {
        'attribs': 5,
        'char_attribs': 4,
        'char_items': 5,
        'characters': 11,
        'event_attribs': 4,
        'event_items': 4,
        'event_triggers': 4,
        'events': 10,
        'item_attribs': 4,
        'items': 11,
        'loc_destinations': 6,
        'loc_items': 5,
        'locations': 10,
        'overall': 5,
        'progress': 8,
        'recipe_attribs': 4,
        'recipe_byproducts': 4,
        'recipe_sources': 5,
        'recipes': 6,
        'scenario_log': 2,
        'user_interactions': 5,
        'win_requirements': 8,
    }
    return lookup[table_name]

if __name__ == '__main__':
    app = Flask(__name__)
    with app.app_context():
        create_all()
