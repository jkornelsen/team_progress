from flask import Flask, g
import logging
import psycopg2

logger = logging.getLogger(__name__)

def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(
            dbname='postgres',
            user='postgres',
            password='admin',
            host='localhost',
            port='5432'
        )
    g.commit_db = True
    return g.db

def close_db(ctx=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

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
        'item',
        'location',
        'overall',
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
        'attribs': 4,
        'char_attribs': 4,
        'char_items': 5,
        'characters': 11,
        'event_attribs': 4,
        'event_triggers': 4,
        'events': 10,
        'item_attribs': 4,
        'items': 10,
        'loc_destinations': 4,
        'loc_items': 5,
        'locations': 8,
        'overall': 4,
        'progress': 7,
        'recipe_attribs': 5,
        'recipe_sources': 6,
        'recipes': 6,
        'user_interactions': 5,
        'win_requirements': 8,
    }
    return lookup[table_name]

if __name__ == '__main__':
    app = Flask(__name__)
    with app.app_context():
        create_all()

