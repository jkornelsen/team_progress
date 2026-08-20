import os
from pathlib import Path
import re
import subprocess
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine

# Define the object once here.
# It isn't "attached" to an app yet.
db = SQLAlchemy()

USE_SQLITE = True

def get_db_uri():
    """Centralized logic for building the connection string."""
    if USE_SQLITE:
        app_dir = Path(__file__).resolve().parent
        project_root = app_dir.parent
        sqlite_dir = project_root / "sqlite_data"
        sqlite_path = (sqlite_dir / "app.db").as_posix()
        os.makedirs(sqlite_dir, exist_ok=True)
        return f"sqlite:///{sqlite_path}"

    # 1. Try to get password from sensitive.py
    try:
        from .sensitive import DB_PASSWORD
    except (ImportError, ValueError):
        # Fallback for local trusted authentication
        DB_PASSWORD = "no password needed with trust"

    # 2. Get other connection details from Environment or Defaults

    user = os.environ.get('DB_USER', "postgres")
    pw = os.environ.get('DB_PASSWORD', DB_PASSWORD)
    host = os.environ.get('DB_HOST', "localhost")
    db_name = os.environ.get('DB_NAME', "app")
    db_port = os.environ.get('DB_PORT', "5432")

    # 3. Construct the SQLAlchemy URI
    return f"postgresql://{user}:{pw}@{host}:{db_port}/{db_name}"

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, _connection_record):
    """Settings for SQLite every time we open a connection."""
    if USE_SQLITE:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

        # Register custom regexp_replace function for SQLite
        def regexp_replace(text, pattern, replacement, _flags=""):
            if text is None:
                return ""
            return re.sub(pattern, replacement, text)

        HOW_MANY_ARGS = 4
        dbapi_connection.create_function(
            "regexp_replace", HOW_MANY_ARGS, regexp_replace)

def start_db():
    if USE_SQLITE:
        return

    env_pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pg_ctl = os.path.join(env_pf, "PostgreSQL", "16", "bin", "pg_ctl.exe")
    data_dir = r"postgres_data"

    if not os.path.exists(pg_ctl):
        print(f"Error: Could not find pg_ctl at {pg_ctl}")
        return

    command = [pg_ctl, "status", "-D", data_dir]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        print("PostgreSQL is running.")
        return
    if result.returncode == 3:
        pass # Simply not running
    else:
        print(f"Status error (Code {result.returncode}): {result.stderr}")
        return

    pid_file = os.path.join(data_dir, "postmaster.pid")
    if os.path.exists(pid_file):
        print("Found stale postmaster.pid. Attempting to start anyway.")

    print("Starting PostgreSQL...")
    try:
        command = [pg_ctl, "start", "-D", data_dir]
        subprocess.run(
            command, check=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
        print("Start issued successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to start PostgreSQL: {e}")

def safe_remove(obj):
    """Removes an object from the session/DB regardless of whether it was saved yet."""
    state = inspect(obj)
    if state.persistent:
        db.session.delete(obj)
    elif state.pending:
        db.session.expunge(obj)
