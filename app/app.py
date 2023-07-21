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
import uuid

from db import db  # This is from db.py, not a standard library.

app = Flask(__name__)
app.config['SECRET_KEY'] = 'team-adventurers'
app.config['TITLE'] = 'Team Adventurers'
app.config['TEMPLATES_AUTO_RELOAD'] = True  # set to False for production
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:admin@localhost/postgres'
db.init_app(app)  # Do this before importing the db model classes.

from src.game_data import GameData
from src.user_interaction import UserInteraction

from src.attrib    import set_routes as _set_routes_attrib
from src.character import set_routes as _set_routes_character
from src.event     import set_routes as _set_routes_event
from src.item      import set_routes as _set_routes_item
from src.location  import set_routes as _set_routes_location
from src.overall   import set_routes as _set_routes_overall
from src.file      import set_routes as _set_routes_file

with app.app_context():
    print("starting app")
    from sqlalchemy import inspect
    mapper = inspect(UserInteraction)
    print(f"Table Name: {mapper.mapped_table.name}")
    print("Column Names:", [c.name for c in mapper.columns])
    db.create_all()

@app.before_request
def before_request():
    print("before_request()")
    # Store data in the g object per user
    g.game_token = session.get('game_token')
    global username  # global to module only -- not as global as g.
    username = session.get(g.game_token, {}).get('username')
    if username:
        # make sure the user is listed in the db as recently connected
        interaction = UserInteraction(username)
        interaction.to_db()
    else:
        print("no user id")

@app.route('/')  # route
def index():  # endpoint
    print("index()")
    # Retrieve game token from URL parameter
    game_token = request.args.get('game_token')
    if game_token:
        session['game_token'] = game_token
    else:
        # Check if game token exists in the session
        game_token = session.get('game_token')
        if not game_token:
            return redirect(url_for('new_session'))
    # Retrieve user ID specific to the game token
    global username
    username = session.get(game_token, {}).get('username')
    if not username:
        return redirect(url_for('set_username'))
    return redirect(url_for('overview'))  # name of endpoint

@app.route('/new-session', methods=['GET', 'POST'])
def new_session():
    print("new_session()")
    if request.method == 'POST':
        game_token = generate_game_token()
        return redirect(url_for('index'))
    return render_template('session/new_session.html')

def generate_game_token():
    print("generate_game_token()")
    # Generate a new unique game token
    game_token = str(uuid.uuid4())
    session['game_token'] = game_token
    return game_token

@app.route('/set-username', methods=['GET', 'POST'])
def set_username():
    game_token = session.get('game_token')
    if request.method == 'POST':
        username = request.form.get('username')
        if username:
            # Store the user ID specific to the game token
            session[game_token] = {'username': username}
            return redirect(url_for('index'))
    return render_template('session/username.html')

@app.route('/session-link')
def get_session_link():
    game_token = session.get('game_token')
    if game_token:
        url = url_for('index', game_token=game_token, _external=True)
        return render_template(
                'session/session_link.html',
                url=url)
    else:
        return "Session not found"

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

# Define the context processor to make values available in all templates.
@app.context_processor
def inject_username():
    return {'current_username': username}

if __name__ == '__main__':
    app.run()

