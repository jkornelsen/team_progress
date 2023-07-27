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
            command = "CREATE TABLE {} (\n{})".format(
                table, schema.strip(chr(13) + chr(10)))
            print(command)
            with db.cursor() as cursor:
                cursor.execute(command)
    db.commit()

if __name__ == '__main__':
    app = Flask(__name__)
    with app.app_context():
        create_all()

