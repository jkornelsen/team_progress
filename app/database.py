from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import make_transient
import os
import subprocess

# Define the object once here. 
# It isn't "attached" to an app yet.
db = SQLAlchemy()

def get_db_uri():
    """Centralized logic for building the connection string."""
    # 1. Try to get password from sensitive.py
    try:
        from .sensitive import DB_PASSWORD
    except (ImportError, ValueError):
        # Fallback for local trusted authentication
        DB_PASSWORD = 'no password needed with trust'

    # 2. Get other connection details from Environment or Defaults

    user = os.environ.get('DB_USER', 'postgres')
    pw = os.environ.get('DB_PASSWORD', DB_PASSWORD) 
    host = os.environ.get('DB_HOST', 'localhost')
    db_name = os.environ.get('DB_NAME', 'app')
    db_port = os.environ.get('DB_PORT', '5432')
    
    # 3. Construct the SQLAlchemy URI
    return f"postgresql://{user}:{pw}@{host}:{db_port}/{db_name}"

def start_postgres():
    env_pf = os.environ.get('ProgramFiles', r'C:\Program Files')
    pg_ctl = os.path.join(env_pf, 'PostgreSQL', '16', 'bin', 'pg_ctl.exe')
    data_dir = r"postgres_data"
    
    if not os.path.exists(pg_ctl):
        print(f"Error: Could not find pg_ctl at {pg_ctl}")
        return

    command = [pg_ctl, "status", "-D", data_dir]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        print("PostgreSQL is running.")
        return
    elif result.returncode == 3:
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

def clone_with_children(obj, overrides):
    """
    Disconnects an object from the DB and resets its state.
    Optionally applies attribute overrides (like a new ID or Name).

    Recursively clones an object and any children defined 
    in get_deep_relationships().
    """
    # 1. Capture children before ghosting the parent
    child_map = {}
    if hasattr(obj, 'get_deep_relationships'):
        for attr, (child_class, fk_field) in obj.get_deep_relationships().items():
            # Use list() to freeze the collection in memory
            child_map[attr] = (list(getattr(obj, attr)), fk_field)

    # 2. Ghost the parent
    db.session.expunge(obj)
    make_transient(obj)
    for key, value in overrides.items():
        setattr(obj, key, value)
    
    db.session.add(obj)
    db.session.flush() # Get the new parent ID

    # 3. Recursively clone children
    for attr, (children, fk_field) in child_map.items():
        for child in children:
            # The child's override is the new parent's ID
            child_overrides = {fk_field: obj.id, 'id': None}
            clone_with_children(child, child_overrides)
            
    return obj
