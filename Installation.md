# Installing

The general steps are to install and configure Python and an SQLite database, then start the app.

If you're interested in a packaged installer, let me know.

Commands are shown for powershell syntax on Windows, but the software should run on Linux or Mac with minimal adjustments. Ask an AI to explain how to run the commands on your specific system.

## I. Normal setup

### 1. Download the project
Download the source code from github as a zip file, then unzip it.

### 2. Activate a Python Virtual Environment
If your system doesn't have it yet, download and install Python. Then, open a command line. Navigate to the project directory and create a new venv:
```
chdir C:/your path to/team_progress
python -m venv venv
venv/Scripts/activate
```

Upgrade pip and install the required packages into this venv:
```powershell
python.exe -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Initialize SQLite database

Create a simple database file:
```
python database_setup.py
```

## 4. Run the app under the venv

```
python run.py
```
Open a web browser to `http://localhost:5000`. Once it works, you're ready to play!

## 5. Other players on LAN

To connect other players at home, grant Python network access. For example, Windows Defender Firewall:

- Check box for Python and Private (home LAN) but not Public
- Set network settings to Private.
- Disconnect and perhaps forget connection, then reconnect.

Then connect from another device:

- Use `ipconfig` to determine your computer's IP address.
- The url is `http://<your.ip.address>:5000/`

---

## II. Alternative Setup: PostgreSQL database

### 1. Change default database
Edit `app/database.py` with a text editor:
```
USE_SQLITE = False
```

### 2. Download PostgreSQL and initialize database
Download and run a PostgreSQL installer, such as EDB. Then, navigate to the project directory and initialize the database:
```
chdir team_progress/app
& "C:/Program Files/PostgreSQL/16/bin/pg_ctl" initdb -U postgres -D postgres_data
& "C:/Program Files/PostgreSQL/16/bin/createuser" --superuser postgres
```

### 3. Start PostgreSQL server
The server needs to be running whenever the app runs.
```
& "C:/Program Files/PostgreSQL/16/bin/pg_ctl" start -D postgres_data
```

### 4. Initialize application database
```
& "C:/Program Files/PostgreSQL/16/bin/psql" -U postgres -d app
create database app with encoding 'UTF8';
```

### 5. Database password

For running on a LAN, trust authentication is fine.
Otherwise, set a password as follows.

1. Rename `app/sensitive.example.py` to `app/sensitive.py` and edit to change the value of `DB_PASSWORD`.
2. Set the password: 
```
alter user postgres with password 'your_password';
```
3. Edit pg_hba.conf to remove trust:
```
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
```
