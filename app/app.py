from datetime import timedelta
from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from flask_caching import Cache
from inspect import signature
import logging
import os
import random
import string
import sys
import uuid

from database import get_db, close_db
from src.cache import init_cache
from src.game_data import GameData
from src.user_interaction import UserInteraction
from src.file import set_routes as _set_file_routes
from src.game_routes import set_routes as _set_game_routes
from src.utils import format_num

app = Flask(__name__)
app.config['TITLE'] = 'Team Progress'
app.config['SECRET_KEY'] = 'team-progress'
app.config['DATA_DIR'] = 'data'
app.config['UPLOAD_DIR'] = os.path.join(app.config['DATA_DIR'], 'uploads')
app.config['TEMPLATES_AUTO_RELOAD'] = True  # set to False for production

def set_up_logging():
    file_handler = logging.FileHandler('app.log')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter('%(filename)s:%(lineno)d  %(message)s'))
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.ERROR)
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[file_handler, stdout_handler])

set_up_logging()
logger = logging.getLogger(__name__)

with app.app_context():
    logger.debug(f"starting app")

@app.before_request
def before_request():
    logger.debug("before_request()")
    if request.endpoint and request.endpoint.startswith('static'):
        return
    if 'game_token' not in session:
        # Create new session automatically. If someone wants to join an
        # existing game, this won't prevent them from doing so afterwards.
        session['game_token'] = generate_game_token()
    g.game_token = session.get('game_token')
    if 'username' not in session:
        # This can be changed later by the user.
        session['username'] = generate_username()
    g.db = get_db()
    UserInteraction.log_visit(session.get('username'))
    GameData()
    g.game_data.overall.load_cached()

def _set_app_routes():
    @app.route('/')  # route name
    def index():  # endpoint name
        logger.debug("index()")
        return redirect(url_for('overview'))  # endpoint name

    @app.route('/join-game', methods=['GET', 'POST'])
    def join_game():
        logger.debug("join_game()")
        # Retrieve game token from URL parameter
        game_token = request.args.get('game_token')
        if game_token:
            session['game_token'] = game_token
            return redirect(url_for('overview'))
        else:
            return "Please include the game token in the URL."

    @app.route('/session-link')
    def get_session_link():
        if 'game_token' not in session:
            return "Session not found"
        game_token = session.get('game_token')
        url = url_for('join_game', game_token=game_token, _external=True)
        return render_template(
            'session/session_link.html',
            url=url)

    @app.route('/change-user', methods=['GET', 'POST'])
    def change_user():
        game_token = session.get('game_token')
        if request.method == 'POST':
            username = request.form.get('username')
            if not username:
                username = generate_username()
            session['username'] = username
            return redirect(url_for('index'))
        return render_template('session/username.html')

    @app.route('/session-users')
    def session_users():
        interactions = UserInteraction.recent_interactions()
        return render_template(
            'session/users.html',
            interactions=interactions)

def generate_game_token():
    """Generate a new unique token to keep games separate."""
    logger.debug("generate_game_token()")
    return str(uuid.uuid4())

def generate_username():
    """Generate a new likely-to-be-unique username."""
    logger.debug("generate_username()")
    consonants = ''.join(c for c in string.ascii_lowercase if c not in 'aeiouyl')
    return ''.join(random.choice(consonants) for _ in range(10))

_set_app_routes()
_set_game_routes(app)
_set_file_routes(app)
init_cache(app)

def get_parameter_name(endpoint):
    """Get first parameter name from the route rule for
    an endpoint."""
    endpoint_obj = app.view_functions.get(endpoint)
    if endpoint_obj is not None:
        sig = signature(endpoint_obj)
        parameters = list(sig.parameters.keys())
        if len(parameters) > 0:
            return parameters[0]
    return None

# Define the context processor to make values available in all templates.
@app.context_processor
def inject_username():
    return {'current_username': session.get('username')}

def formatNum_filter(value):
    return format_num(value)

app.jinja_env.filters['formatNum'] = formatNum_filter
app.jinja_env.globals['MAX_INT_32'] = 2**31 - 1

@app.errorhandler(TypeError)
def handle_type_error(ex):
    logger.exception(ex)
    return render_template(
        'error.html',
        message="An error occurred.",
        details=str(ex))

@app.teardown_appcontext
def teardown(ctx):
    close_db()

if __name__ == '__main__':
    app.run()

