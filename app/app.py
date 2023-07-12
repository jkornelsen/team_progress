from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)
import json
import threading
from datetime import timedelta
import time
import uuid

from entities.attrib import Attrib, set_routes as _set_attrib_routes
from entities.character import Character, set_routes as _set_character_routes
from entities.event import Event, set_routes as _set_event_routes
from entities.item import Item, set_routes as _set_item_routes
from entities.location import Location, set_routes as _set_location_routes
from entities.overall import Overall, set_routes as _set_overall_routes

app = Flask(__name__)
app.config['SECRET_KEY'] = 'team-adventurers'
app.config['TITLE'] = 'Team Adventurers'
app.config['TEMPLATES_AUTO_RELOAD'] = True  # set to False for production

class GameData:
    def __init__(self):
        self.attribs = Attrib.instances
        self.characters = Character.instances
        self.events = Event.instances
        self.items = Item.instances
        self.locations = Location.instances
        self.overall = Overall

    def to_json(self):
        return {
            'attribs': [attrib.to_json() for attrib in self.attribs],
            'locations': [location.to_json() for location in self.locations],
            'items': [item.to_json() for item in self.items],
            'characters': [character.to_json() for character in self.characters],
            'events': [event.to_json() for event in self.events],
            'overall': self.overall.to_json()
        }

    @classmethod
    def from_json(cls, data):
        game_data = cls()
        # Load in this order to correctly get references to other entities. 
        game_data.attribs = Attrib.list_from_json(data['attribs'])
        game_data.locations = Location.list_from_json(data['locations'])
        game_data.items = Item.list_from_json(data['items'])
        game_data.characters = Character.list_from_json(data['characters'])
        game_data.events = Event.list_from_json(data['events'])
        game_data.overall = Overall.from_json(data['overall'])
        return game_data

def generate_game_token():
    # Generate a new unique game token
    game_token = str(uuid.uuid4())
    # Initialize the game data for the new game token
    game_data = GameData()
    Attrib.game_data = game_data
    Character.game_data = game_data
    Event.game_data = game_data
    Item.game_data = game_data
    Location.game_data = game_data
    Overall.game_data = game_data
    # Associate the game data with the game token
    session['game_token'] = game_token
    session['game_data'] = json.dumps(game_data)
    return game_token

@app.route('/new-session', methods=['GET', 'POST'])
def new_session():
    if request.method == 'POST':
        game_token = generate_game_token()
        return redirect(url_for('overview', game_token=game_token))
    return render_template('session/new_session.html')

# When a user joins the game token or logs in
def join_game_token(user_id):
    #game_token_users = set(session.get('game_token_users', []))
    game_token_users = set(session.get('game_token_users', {}).get(game_token, []))
    game_token_users.add(user_id)
    game_token_data = session.get('game_token_users', {})
    game_token_data[game_token] = list(game_token_users)
    session['game_token_users'] = game_token_data

# Store game_data in the g object per user
@app.before_request
def before_request():
    game_token = session.get('game_token')
    #user_id = session.get('user_id')
    user_id = session.get(game_token, {}).get('user_id')
    if user_id:
        g.game_data = game_data
        join_game_token(game_token, user_id)
    else:
        g.game_data = None

@app.route('/')  # route
def index():  # endpoint
    # Retrieve game token from URL parameter
    game_token = request.args.get('game_token')
    # If game token is provided in the URL, store it in the session
    if game_token:
        session['game_token'] = game_token
    else:
        # Check if game token exists in the session
        game_token = session.get('game_token')
        if not game_token:
            return redirect(url_for('new_session'))
    # Retrieve user ID specific to the game token
    user_id = session.get(game_token, {}).get('user_id')
    if not user_id:
        return redirect(url_for('set_username'))
    return redirect(url_for('overview'))  # name of endpoint

@app.route('/set-username', methods=['GET', 'POST'])
def set_username():
    game_token = session.get('game_token')
    if request.method == 'POST':
        user_id = request.form.get('username')
        if user_id:
            #session['user_id'] = user_id
            # Store the user ID specific to the game token
            session[game_token] = {'user_id': user_id}
            return redirect(url_for('overview'))
    return render_template('session/username.html')

@app.route('/session-link')
def get_session_link():
    game_token = session.get('game_token')
    if game_token:
        url = url_for('index', game_token=game_token, _external=True)
        return render_template('session/session_link.html', url=url)
    else:
        return "Session not found"

@app.route('/change_user')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('set_username'))

_set_attrib_routes(app)
_set_character_routes(app)
_set_event_routes(app)
_set_item_routes(app)
_set_location_routes(app)
_set_overall_routes(app)

@app.route('/configure')
def configure():
    file_message = session.pop('file_message', False)
    game_data = json.loads(session['game_data'])
    return render_template(
        'configure/index.html',
        game=game_data,
        file_message=file_message)

FILEPATH = 'data/data.json'

@app.route('/save_to_file')
def save_to_file():
    game_data = g.game_data
    data_to_save = game_data.to_json()
    with open(FILEPATH, 'w') as outfile:
        json.dump(data_to_save, outfile, indent=4)
    session['file_message'] = 'Saved to file.'
    return redirect(url_for('configure'))

@app.route('/load_from_file')
def load_from_file():
    with open(FILEPATH, 'r') as infile:
        data = json.load(infile)
        game_data = GameData.from_json(data)
    g.game_data = game_data
    session['file_message'] = 'Loaded from file.'
    return redirect(url_for('configure'))

if __name__ == '__main__':
    app.run()

