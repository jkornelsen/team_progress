"""
The main entry point for the server app.
Sets up routes that get called by clients.
"""
from inspect import signature
import io
import logging
import os
import random
import re
import string
import sys
import uuid

import bleach
from bleach.css_sanitizer import CSSSanitizer
from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for
    )
from markupsafe import Markup

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
app.config['DATA_DIR'] = 'data_files'
app.config['UPLOAD_DIR'] = os.path.join(app.config['DATA_DIR'], 'uploads')
app.config['TEMPLATES_AUTO_RELOAD'] = True  # set to False for production

def set_up_logging():
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    file_handler = logging.FileHandler('app.log', encoding='utf-8')
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
    logger.debug("starting app")

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
        from src.character import Character
        g.game_data.from_db_flat([Character])
        if request.method == 'GET':
            return render_template(
                'session/username.html',
                characters=g.game_data.characters
                )
        username = request.form.get('username')
        if not username:
            username = generate_username()
        session['username'] = username
        return redirect(url_for('index'))

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

def _set_filters():
    """Values available in all templates."""
    @app.context_processor
    def inject_username():
        return {'current_username': session.get('username')}

    @app.template_filter('formatNum')
    def format_num_filter(value):
        return format_num(value)

    @app.template_filter('htmlify')
    def htmlify_filter(html):
        # color tags
        html = re.sub(
            r'<c\s*=\s*["\']?([^"\'>]+)["\']?\s*>',
            r'<span style="color:\1;">', html)
        html = re.sub(r'</c\s*>', r'</span>', html)
        # remove the first newline after <pre>
        html = re.sub(
            r'(<pre[^>]*>(?:<[^>]+>)*)\r?\n',
            r'\1', html)
        # add styling to <pre>
        html = re.sub(
            r'(<pre[^>]*)>', 
            r'\1 style="white-space: pre-wrap; word-wrap: break-word;'
            r' overflow-wrap: break-word;">', 
            html)
        # handle styles safely
        css_sanitizer = CSSSanitizer(
                allowed_css_properties=[
                'color', 'white-space', 'word-wrap', 'overflow-wrap'])
        html = bleach.clean(
            html,
            tags={'a', 'b', 'i', 'span', 'pre'},
            attributes={
                'a': ['href', 'title'],
                'span': ['style'],
                'pre': ['style']
                },
                css_sanitizer=css_sanitizer
            )
        def sanitize_href(match):
            """Limit URLs to within the app."""
            href = match.group(2).strip()
            if not re.match(r'^/[a-zA-Z0-9/=?]*["\']?$', href):
                return 'href="#"'
            return match.group(0)

        html = re.sub(r'href\s*=\s*(["\']?)([^>]+)', sanitize_href, html)
        html = re.sub(r'\r?\n', '<br>', html)
        html = Markup(html)  # so Flask will consider the content safe
        return html

    @app.template_filter('removeLinks')
    def remove_links_filter(html):
        html = re.sub(r'<a\s+[^>]*>(.*?)<\/a>', r'\1', html)
        return html

    app.jinja_env.globals['getattr'] = getattr
    app.jinja_env.globals['max'] = max
    app.jinja_env.globals['MAX_INT_32'] = 2**31 - 1

_set_app_routes()
_set_game_routes(app)
_set_file_routes(app)
_set_filters()
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

@app.errorhandler(Exception)
def handle_exception(ex):
    logger.exception(ex)
    return render_template(
        'error.html',
        message="An unexpected error occurred.",
        details=str(ex))

@app.errorhandler(404)
def page_not_found(e):
    logger.error(f"404 Error: URL {request.url} not found")
    return "Page not found", 404

@app.teardown_appcontext
def teardown(ctx):  # pylint: disable=unused-argument
    close_db()

if __name__ == '__main__':
    app.run()
