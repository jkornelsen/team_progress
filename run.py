import logging
import os
import subprocess
from app import create_app

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def start_postgres():
    envProgramFiles = os.environ.get('ProgramFiles', r'C:\Program Files')
    pg_ctl = rf"{envProgramFiles}\PostgreSQL\16\bin\pg_ctl.exe"
    data_dir = r"postgres_data"
    
    if not os.path.exists(pg_ctl):
        print(f"Error: Could not find pg_ctl at {pg_ctl}")
        return

    try:
        command = [pg_ctl, "status", "-D", data_dir]
        result = subprocess.run(
            command, check=True, capture_output=True, text=True)
        if result.returncode == 0:
            print("PostgreSQL is running.")
            return
    except subprocess.CalledProcessError as e:
        print(f"Failed to check PostgreSQL status: {e}")

    pid_file = os.path.join(data_dir, "postmaster.pid")
    if os.path.exists(pid_file):
        print("Error: PostgreSQL was not shut down properly.")
        return

    try:
        command = [pg_ctl, "start", "-D", data_dir]
        subprocess.run(
            command, check=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
        print("Starting PostgreSQL.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to start PostgreSQL: {e}")

app = create_app()

if __name__ == "__main__":
    # The host='0.0.0.0' allows it to be seen on your local network if needed
    start_postgres()
    app.run(
        debug=True, use_reloader=False, use_debugger=False,
        host='127.0.0.1', port=5000)
