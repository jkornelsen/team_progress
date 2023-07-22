from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import declarative_base

DB_INIT_STR = 'postgresql://postgres:admin@localhost/postgres'

print(f"{__name__}: creating db and Base")
db = SQLAlchemy()
Base = declarative_base()

def create_all():
    print(f"{__name__}: create_all BEGIN")
    # Creating an sqlalchemy engine directly works well for creating tables,
    # but it causes problems with multiple flask session requests.
    # Apparently that's what flask_sqlalchemy helps with.
    from sqlalchemy import create_engine
    engine = create_engine(
        DB_INIT_STR,
        connect_args = {"port": 5432},
        echo="debug",
        echo_pool=True
    )
    import src.attrib
    import src.character
    import src.event
    import src.item
    import src.location
    import src.overall
    import src.user_interaction
    Base.metadata.create_all(bind=engine)
    print(f"{__name__}: create_all END")
