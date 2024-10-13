# Installing
## I. Overview

The basic steps are to download, install and configure python
and a postgres database. Then start the app.

I'm not sure if this will be too hard for most people or not.
It should be fine for those who have worked in programming
or system administration.
But even people who just regularly play computer games may have developed
enough skills to install, run commands, and edit configuration files.

Commands are given using powershell syntax on windows,
but the software should run on other systems such as Linux
with only minimal adjustments,
such as paths and not needing `&` to run something.

## II. Python Virtual Environment Setup

### 1. Install Python
If your system doesn't have it yet, download and install Python.

### 2. Create and Activate a Virtual Environment
Now, open a command line.
Navigate to the project directory and create a new venv:
```
chdir team_progress/app
rm -Path ./venv -Recurse -Force[^1]
python -m venv venv
./venv/Scripts/activate
```

### 3. Upgrade Pip and Install Dependencies
Upgrade pip and install the required packages into this venv:
```
python.exe -m pip install --upgrade pip
pip install flask
pip install flask-caching
pip install psycopg2
pip install bleach
```

## III. Database Installation and Setup

### 1. Download PostgreSQL
Download and run the PostgreSQL EDB installer.

### 2. Initialize Database
Navigate to the project directory and initialize the PostgreSQL database:
```
chdir team_progress/app
& "C:/Program Files/PostgreSQL/16/bin/pg_ctl" initdb -U postgres -D postgres_data
& "C:/Program Files/PostgreSQL/16/bin/createuser" --superuser postgres
```

### 3. Start PostgreSQL server
The server needs to be running whenever the app runs,
so either enter this command each time, or set to run automatically as a service.
```
& "C:/Program Files/PostgreSQL/16/bin/pg_ctl" start -D postgres_data
```

### 4. Set up database
```
& "C:/Program Files/PostgreSQL/16/bin/psql" -U postgres -d app
create database app with encoding 'UTF8' template template0;
```

### 5. Create tables
```
drop schema public cascade; create schema public;[^2]
python database.py
```

## IV. Run the app

```
chdir team_progress/app
venv/Scripts/activate
python app.py
```
Open a web browser to http://localhost:5000. 
If it works, you're ready to play!

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
