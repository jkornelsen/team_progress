import logging
import sys
from flask import Flask
from sqlalchemy import text
from app import create_app
from app.database import start_postgres
from app.models import db, Entity, Overall, GENERAL_ID

logger = logging.getLogger(__name__)

def setup_database(app: Flask, drop_first=False):
    """
    Creates all tables based on SQLAlchemy models.
    This should be run once during application deployment or 
    whenever the schema changes.
    If drop_first is True, it wipes the entire database schema first.
    """
    def log_and_print(msg, level="info"):
        getattr(logger, level)(msg)
        print(msg)

    with app.app_context():
        if drop_first:
            log_and_print("Wiping all existing schema.")
            # We use a raw SQL command to drop the public schema and recreate it.
            # This also deletes old tables the code no longer uses.
            db.session.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
            db.session.execute(text("GRANT ALL ON SCHEMA public TO public;"))
            db.session.commit()

        log_and_print("Initializing tables.")
        db.create_all()
        log_and_print("Finished.")

if __name__ == "__main__":
    # This allows running 'python database_setup.py' from the terminal
    # to perform a fresh schema creation.
    reset_mode = "--wipe" in sys.argv
    app = create_app()
    start_postgres()
    setup_database(app, drop_first=reset_mode)
