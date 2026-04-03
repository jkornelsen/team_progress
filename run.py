import logging
import os
import subprocess
from app import create_app

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

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

app = create_app()

if __name__ == "__main__":
    # The host='0.0.0.0' allows it to be seen on your local network if needed
    start_postgres()
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
