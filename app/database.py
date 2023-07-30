import psycopg2
from flask import Flask, g

def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(
            dbname='postgres',
            user='postgres',
            password='admin',
            host='localhost',
            port='5432'
        )
    return g.db

def close_db(ctx=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def pretty(text):
    """Pretty-print SQL by indenting consistently and removing extra
    newlines."""
    text = text.strip('\r\n')
    lines = text.split('\n')
    indented_lines = [' ' * 8 + line.strip() for line in lines]
    indented_text = '\n'.join(indented_lines)
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
            print(command)
            with db.cursor() as cursor:
                cursor.execute(command)
    db.commit()

def column_counts(table_name):
    """
    Generate the values with:
        SELECT '''' || table_name || ''': ' || COUNT(column_name) || ','
        FROM information_schema.columns
        WHERE table_schema = 'public'
        GROUP BY table_name
        ORDER BY table_name;
    """
    lookup = {
        'attribs': 4,
        'char_attribs': 3,
        'char_items': 3,
        'characters': 8,
        'events': 7,
        'item_attribs': 3,
        'item_sources': 3,
        'items': 8,
        'location_destinations': 3,
        'locations': 4,
        'overall': 3,
        'progress': 12,
        'user_interactions': 6,
        'winning_items': 3,
    }
    return lookup[table_name]

if __name__ == '__main__':
    app = Flask(__name__)
    with app.app_context():
        create_all()

