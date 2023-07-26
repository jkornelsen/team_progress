import psycopg2
from flask import g

def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(
            dbname='postgresql',
            user='postgres',
            password='admin',
            host='localhost',
            port='5432'
        )
        #g.db.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def create_all():
    import importlib
    module_names = [
        'attrib',
        'character',
        'event',
        'item',
        'location',
        'overall',
        'user_interaction',
    ]
    for module_name in module_names:
        module = importlib.import_module(f'src.{module_name}')
        for table, schema in module.tables_to_create():
            with g.db.cursor() as cursor:
                cursor.execute(f"""
    CREATE TABLE {table} (
        {schema.strip()}
    )
"""
