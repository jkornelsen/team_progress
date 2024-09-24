import datetime
from json import JSONDecodeError
import logging
import os
import tempfile

from flask import (
    g, json, redirect, render_template, request, send_file,
    session, url_for)
from werkzeug.utils import secure_filename

from database import set_autocommit
from .game_data import GameData
from .db_serializable import DbSerializable
from .overall import Overall
from .utils import RequestHelper

logger = logging.getLogger(__name__)

tables_to_create = {
    'scenario_log': """
        filename varchar(50) NOT NULL,
        times_loaded integer NOT NULL,
        UNIQUE (filename)
        """
    }

def set_routes(app):
    @app.route('/configure')
    def configure_index():
        file_message = session.pop('file_message', False)
        g.game_data.entity_names_from_db()
        for session_key in (
                'last_affected_char_id',
                'last_char_id',
                'last_loc_id',
                'default_move_char',
                'default_pickup_char',
                'default_movingto_char',
                'default_slot',
            ):
            session.pop(session_key, None)
        return render_template(
            'configure/index.html',
            game_data=g.game_data,
            file_message=file_message
            )

    @app.route('/save_to_file')
    def save_to_file():
        g.game_data.load_for_file()
        data_to_save = g.game_data.dict_for_json()
        filename = generate_filename(g.game_data.overall.title)
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            filepath = temp_file.name
            json.dump(data_to_save, temp_file, indent=4)
        return send_file(filepath, as_attachment=True, download_name=filename)

    @app.route('/load_from_file', methods=['GET', 'POST'])
    def load_from_file():
        UPLOAD_DIR = app.config['UPLOAD_DIR']
        if request.method == 'GET':
            file_message = session.pop('file_message', False)
            return render_template(
                'session/upload.html',
                file_message=file_message)
        uploaded_file = request.files['file']
        file_message = ''
        if uploaded_file.filename == '':
            file_message = "Please upload a file."
        elif not uploaded_file.filename.endswith('.json'):
            file_message = "Please upload a file with .json extension."
        if file_message:
            session['file_message'] = file_message
            return redirect(url_for('load_from_file'))
        filename = secure_filename(uploaded_file.filename)
        filepath = os.path.join(UPLOAD_DIR, filename)
        uploaded_file.save(filepath)
        try:
            load_data_from_file(filepath)
        except Exception:
            logger.exception("")
            return render_template(
                'error.html',
                message="An error occurred while processing the file.")
        # Get rid of old files
        MAX_FILE_AGE = datetime.timedelta(minutes=5)
        current_time = datetime.datetime.now()
        for filename in os.listdir(UPLOAD_DIR):
            file_path = os.path.join(UPLOAD_DIR, filename)
            file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
            file_age = current_time - file_mtime
            if file_age > MAX_FILE_AGE:
                os.remove(file_path)
                logger.info("Deleted %s (age: %s)", filename, file_age)
        return redirect(url_for('configure_index'))

    @app.route('/browse_scenarios', methods=['GET', 'POST'])
    def browse_scenarios():
        DATA_DIR = app.config['DATA_DIR']
        if request.method == 'GET':
            scenarios = []
            popularity = {}
            rows = DbSerializable.execute_select("""
                SELECT * FROM scenario_log
                """)
            for row in rows:
                popularity[row.filename] = row.times_loaded
            for filename in os.listdir(DATA_DIR):
                if filename.endswith('.json'):
                    filepath = os.path.join(DATA_DIR, filename)
                    try:
                        scenario = load_scenario_metadata(filepath)
                    except JSONDecodeError as e:
                        return render_template(
                            'error.html',
                            message=f"Error reading {filename}",
                            details=str(e))
                    scenario['filename'] = filename
                    scenario['popularity'] = popularity.get(filename, 0)
                    scenarios.append(scenario)
            req = RequestHelper('args')
            sort_by = req.get_str('sort_by', 'filename')
            if sort_by == 'filename':
                scenarios = sorted(scenarios, key=lambda x: x['filename'])
            elif sort_by == 'title':
                scenarios = sorted(scenarios, key=lambda x: x['title'])
            elif sort_by == 'filesize':
                scenarios = sorted(scenarios, key=lambda x: x['filesize'], reverse=True)
            elif sort_by == 'popularity':
                scenarios = sorted(scenarios, key=lambda x: x['popularity'], reverse=True)
            return render_template(
                'configure/scenarios.html',
                scenarios=scenarios,
                sort_by=sort_by)
        scenario_file = request.form.get('scenario_file')
        scenario_title = request.form.get('scenario_title')
        if not scenario_file:
            return render_template(
                'error.html',
                message="No scenario file was specified.")
        filepath = os.path.join(DATA_DIR, scenario_file)
        try:
            load_data_from_file(filepath)
        except Exception as ex:
            logger.exception("")
            return render_template(
                'error.html',
                message=f"Could not load \"{scenario_title}\".",
                details=str(ex))
        DbSerializable.execute_change("""
            INSERT INTO scenario_log (filename, times_loaded)
            VALUES (%s, 1)  -- Start with 1 on the first load
            ON CONFLICT (filename) DO UPDATE
            SET times_loaded = scenario_log.times_loaded + 1
            """, [scenario_file])
        session['file_message'] = 'Loaded scenario "{}"'.format(scenario_title)
        return redirect(url_for('configure_index'))

    @app.route('/blank_scenario')
    def blank_scenario():
        GameData.clear_db_for_token()
        GameData()
        g.game_data.to_db()
        session['file_message'] = 'Starting game with default setup.'
        return redirect(url_for('configure_index'))

def load_data_from_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as infile:
        data = json.load(infile)
    g.game_data.from_json(data)
    GameData.clear_db_for_token()
    set_autocommit(False)
    try:
        DbSerializable.execute_change("BEGIN")
        DbSerializable.execute_change("SET CONSTRAINTS ALL DEFERRED")
        g.game_data.to_db()
        g.db.commit()
        session['file_message'] = 'Loaded from file.'
    except Exception as ex:
        g.db.rollback()
        raise ex
    finally:
        set_autocommit(True)

def load_scenario_metadata(filepath):
    with open(filepath, 'r', encoding='utf-8') as infile:
        data = json.load(infile)
    overall = Overall.from_data(data['overall'])
    return {
        'title': overall.title,
        'description': overall.description,
        'filesize': os.path.getsize(filepath)
        }

def generate_filename(title):
    # Remove special characters and replace with '_'
    filename = ''.join(c if c.isalnum() else '_' for c in title)
    MAX_FILENAME_LENGTH = 30
    filename = filename[:MAX_FILENAME_LENGTH]  # Limit filename length
    filename = "{}.json".format(filename)
    return filename
