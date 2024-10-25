import datetime
from json import JSONDecodeError
import logging
import os
import re
import tempfile

from flask import (
    g, json, redirect, render_template, request, send_file,
    session, url_for)
from werkzeug.utils import secure_filename

from database import set_autocommit
from .game_data import GameData
from .db_serializable import DbSerializable
from .overall import Overall
from .utils import LinkLetters, RequestHelper

logger = logging.getLogger(__name__)

DEFAULT_SCENARIO = "00_Default.json"
DATA_DIR = None

def set_routes(app):
    global DATA_DIR
    DATA_DIR = app.config['DATA_DIR']

    @app.route('/configure')
    def configure_index():
        logger.debug("%s\nconfigure_index()", "-" * 80)
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
        logger.debug("%s\nsave_to_file()", "-" * 80)
        g.game_data.load_for_file()
        data_to_save = g.game_data.dict_for_json()
        json_output = json.dumps(data_to_save, indent=4, default=str)
        json_output = flatten_tuples(json_output)
        filename = generate_filename(g.game_data.overall.title)
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            filepath = temp_file.name
            temp_file.write(json_output)
        return send_file(filepath, as_attachment=True, download_name=filename)

    @app.route('/load_from_file', methods=['GET', 'POST'])
    def load_from_file():
        logger.debug("%s\nload_from_file()", "-" * 80)
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
            load_file_into_db(filepath)
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
        return redirect(url_for('overview'))

    @app.route('/browse_scenarios', methods=['GET', 'POST'])
    def browse_scenarios():
        logger.debug("%s\nbrowse_scenarios()", "-" * 80)
        if request.method == 'GET':
            scenarios = []
            for filename in os.listdir(DATA_DIR):
                if filename.endswith('.json') and filename != DEFAULT_SCENARIO:
                    filepath = os.path.join(DATA_DIR, filename)
                    try:
                        scenario = load_scenario_metadata(filepath)
                    except JSONDecodeError as e:
                        return render_template(
                            'error.html',
                            message=f"Error reading {filename}",
                            details=str(e))
                    scenario['filename'] = filename
                    scenarios.append(scenario)
            req = RequestHelper('args')
            sort_by = req.get_str('sort_by', 'filename')
            reverse = sort_by in ('filesize', 'multiplayer')
            scenarios = sorted(
                scenarios, key=lambda x: x[sort_by], reverse=reverse)
            return render_template(
                'configure/scenarios.html',
                scenarios=scenarios,
                sort_by=sort_by,
                link_letters=LinkLetters('mos'))
        scenario_file = request.form.get('scenario_file')
        scenario_title = request.form.get('scenario_title')
        if not scenario_file:
            return render_template(
                'error.html',
                message="No scenario file was specified.")
        filepath = os.path.join(DATA_DIR, scenario_file)
        try:
            load_file_into_db(filepath)
        except Exception as ex:
            logger.exception("")
            return render_template(
                'error.html',
                message=f"Could not load \"{scenario_title}\".",
                details=str(ex))
        session['file_message'] = 'Loaded scenario "{}"'.format(scenario_title)
        return redirect(url_for('overview'))

    @app.route('/blank_scenario')
    def blank_scenario():
        logger.debug("%s\nblank_scenario()", "-" * 80)
        load_file_into_db(os.path.join(DATA_DIR, DEFAULT_SCENARIO))
        session['file_message'] = 'Starting game with default setup.'
        return redirect(url_for('configure_index'))

def default_scenario():
    load_file(os.path.join(DATA_DIR, DEFAULT_SCENARIO))

def load_file_into_db(filepath):
    """Load game data from file and store into db."""
    load_file(filepath)
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

def load_file(filepath):
    """Load game data from file."""
    with open(filepath, 'r', encoding='utf-8') as infile:
        data = json.load(infile)
    g.game_data.from_json(data)

def load_scenario_metadata(filepath):
    with open(filepath, 'r', encoding='utf-8') as infile:
        data = json.load(infile)
    overall = Overall.from_data(data['overall'])
    metadata = {
        attr: getattr(overall, attr)
        for attr in ['title', 'description', 'multiplayer', 'progress_type']
        }
    metadata['filesize'] = os.path.getsize(filepath)
    return metadata

def generate_filename(title):
    # Remove special characters and replace with '_'
    filename = ''.join(c if c.isalnum() else '_' for c in title)
    MAX_FILENAME_LENGTH = 30
    filename = filename[:MAX_FILENAME_LENGTH]  # Limit filename length
    filename = "{}.json".format(filename)
    return filename

def flatten_tuples(json_output):
    """Condense json output a bit. For example, change [0,0] to be on a single
    line instead of four lines.
    """
    def format_two_element_lists(match):
        contents = match.group(1) + ", " + match.group(2)
        return f'[{contents}]'

    json_output = re.sub(
        r'\[\s*([\d.]+|".*?")\s*,\s*([\d.]+|".*?")\s*\]',
        format_two_element_lists, json_output)
    def format_tuple(match):
        contents = match.group(2).replace("\n", "").replace(" ", "")
        formatted_contents = ",".join(contents.split(","))
        return f'"{match.group(1)}": [{formatted_contents}]'

    tuple_keys = ["door1", "door2", "dimensions", "excluded", "position"]
    key_pattern = "|".join(tuple_keys)
    pattern = rf'"({key_pattern})":\s*\[(.*?)\]'
    json_output = re.sub(pattern, format_tuple, json_output, flags=re.DOTALL)
    return json_output

