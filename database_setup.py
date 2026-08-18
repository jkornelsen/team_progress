import logging
import os
import sys
from flask import Flask
from sqlalchemy import text
from app import create_app
from app.database import USE_SQLITE, db, start_db

logger = logging.getLogger(__name__)

def setup_database(app: Flask, drop_first=False):
    """Creates all tables based on SQLAlchemy models.
    If drop_first is True, it wipes the database schema first.
    """
    def log_and_print(msg, level="info"):
        getattr(logger, level)(msg)
        print(msg)

    with app.app_context():
        if drop_first:
            log_and_print("Wiping all existing schema.")
            if USE_SQLITE:
                db.drop_all()
            else:
                # For PostgreSQL we use a raw SQL command to drop the
                # public schema and recreate it.
                db.session.execute(
                    text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
                db.session.execute(
                    text("GRANT ALL ON SCHEMA public TO public;"))
                db.session.commit()

        log_and_print("Initializing tables.")
        db.create_all()
        log_and_print("Finished.")

if __name__ == "__main__":
    # This allows running 'python database_setup.py' from the terminal
    # to perform a fresh schema creation.
    reset_mode = "--wipe" in sys.argv
    app = create_app()
    start_db()
    setup_database(app, drop_first=reset_mode)
