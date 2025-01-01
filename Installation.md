# Installing

The basic steps are to install and configure python
and a postgres database. Then start the app.

Commands are given using powershell syntax on windows,
but the software should run on other systems such as Linux
with only minimal adjustments,
such as paths and not needing `&` to run something.

## I. Python Virtual Environment setup

### 1. Create and activate a Virtual Environment
If your system doesn't have it yet, download and install Python.
Then, open a command line.
Navigate to the project directory and create a new venv:
```
chdir team_progress/app
rm -Path ./venv -Recurse -Force[^1]
python -m venv venv
venv/Scripts/activate
```

### 2. Upgrade Pip and install dependencies
Upgrade pip and install the required packages into this venv:
```
python.exe -m pip install --upgrade pip
pip install flask
pip install flask-caching
pip install psycopg2
pip install bleach
pip install tinycss2
```

## II. Database installation and setup

### 1. Download PostgreSQL and initialize database
Download and run a PostgreSQL installer, such as EDB. Then, navigate to the project directory and initialize the database:
```
chdir team_progress/app
& "C:/Program Files/PostgreSQL/16/bin/pg_ctl" initdb -U postgres -D postgres_data
& "C:/Program Files/PostgreSQL/16/bin/createuser" --superuser postgres
```

### 2. Start PostgreSQL server
The server needs to be running whenever the app runs,
so either enter this command each time, or set to run automatically as a service.
```
& "C:/Program Files/PostgreSQL/16/bin/pg_ctl" start -D postgres_data
```

### 3. Set up tables
```
& "C:/Program Files/PostgreSQL/16/bin/psql" -U postgres -d app
create database app with encoding 'UTF8' template template0;
drop schema public cascade;[^2]
python database.py
```

## III. Run the app

```
chdir team_progress/app
venv/Scripts/activate
python app.py
```
Open a web browser to http://localhost:5000. 
If it works, you're ready to play!

## IV. Other players on LAN

### 1. Allow network access

Grant Python access. For example, Windows Defender Firewall:
- Check box for Python and Private (home LAN) but not Public
- Set network settings to Private.
- Disconnect and perhaps forget connection, then reconnect.

### 2. Connect from another device

- Use `ipconfig` to determine your computer's IP address.
- The url is `http://<ip-address>:5000/`
- Pass this url to a phone with https://www.qr-code-generator.com/

## V. Database password

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

[^1]: Only needed if you've run the commands before and need to rebuild the venv.
[^2]: Only needed if you've run the commands before and need to rebuild the tables.
