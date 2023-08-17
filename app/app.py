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
from inspect import signature
import os
import uuid

from database import get_db, close_db

app = Flask(__name__)
app.config['TITLE'] = 'Team Progress'
app.config['SECRET_KEY'] = 'team-progress'
app.config['DATA_DIR'] = 'data'
app.config['UPLOAD_DIR'] = os.path.join(app.config['DATA_DIR'], 'uploads')
app.config['TEMPLATES_AUTO_RELOAD'] = True  # set to False for production

from src.user_interaction import UserInteraction

from src.attrib    import set_routes as _set_routes_attrib
from src.character import set_routes as _set_routes_character
from src.event     import set_routes as _set_routes_event
from src.item      import set_routes as _set_routes_item
from src.location  import set_routes as _set_routes_location
from src.overall   import set_routes as _set_routes_overall
from src.file      import set_routes as _set_routes_file

with app.app_context():
    print(f"{__name__}: starting app")

@app.before_request
def before_request():
    print("before_request()")
    if request.endpoint and (
            request.endpoint in ('set_username',)
            or request.endpoint.startswith('static')):
        # Maybe we just ran this method, so don't keep repeating.
        return
    if 'game_token' not in session:
        # Create new session automatically. If someone wants to join an
        # existing game, this won't prevent them from doing so afterwards.
        generate_game_token()
    g.game_token = session.get('game_token')
    if 'username' not in session:
        return redirect(url_for('set_username'))
    g.db = get_db()
    UserInteraction.log_visit(session.get('username'))

@app.route('/')  # route
def index():  # endpoint
    print("index()")
    return redirect(url_for('overview'))  # name of endpoint

def generate_game_token():
    """Generate a new unique token to keep games separate."""
    print("generate_game_token()")
    session['game_token'] = str(uuid.uuid4())

@app.route('/join-game', methods=['GET', 'POST'])
def join_game():
    print("join_game()")
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

@app.route('/set-username', methods=['GET', 'POST'])
def set_username():
    game_token = session.get('game_token')
    if request.method == 'POST':
        username = request.form.get('username')
        if username:
            # Store the user ID specific to the game token
            session['username'] = username
            return redirect(url_for('index'))
    return render_template('session/username.html')

@app.route('/change_user')
def logout():
    session.pop('username', None)
    return redirect(url_for('set_username'))

_set_routes_attrib(app)
_set_routes_character(app)
_set_routes_event(app)
_set_routes_item(app)
_set_routes_location(app)
_set_routes_overall(app)
_set_routes_file(app)

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

@app.teardown_appcontext
def teardown(ctx):
    close_db()

if __name__ == '__main__':
    app.run()

