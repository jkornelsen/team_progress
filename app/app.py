from datetime import timedelta
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
from pymongo import MongoClient
import json
import threading
import time
import uuid

from src.attrib import Attrib, set_routes as _set_attrib_routes
from src.character import Character, set_routes as _set_character_routes
from src.event import Event, set_routes as _set_event_routes
from src.item import Item, set_routes as _set_item_routes
from src.location import Location, set_routes as _set_location_routes
from src.overall import Overall, set_routes as _set_overall_routes
from src.user_interaction import UserInteraction

app = Flask(__name__)
app.config['SECRET_KEY'] = 'team-adventurers'
app.config['TITLE'] = 'Team Adventurers'
app.config['TEMPLATES_AUTO_RELOAD'] = True  # set to False for production
app.config['MONGO_URI'] = 'mongodb://localhost:27017/team_adventurers_db'
client = MongoClient(app.config['MONGO_URI'])
db = client['team_adventurers_db']

class GameData:
    def __init__(self):
        self.attribs = Attrib.instances
        self.characters = Character.instances
        self.events = Event.instances
        self.items = Item.instances
        self.locations = Location.instances
        self.overall = Overall.from_db()
        Attrib.game_data = self
        Character.game_data = self
        Event.game_data = self
        Item.game_data = self
        Location.game_data = self
        Overall.game_data = self

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
        instance = cls()
        # Load in this order to correctly get references to other entities. 
        instance.attribs = Attrib.list_from_json(data['attribs'])
        instance.locations = Location.list_from_json(data['locations'])
        instance.items = Item.list_from_json(data['items'])
        instance.characters = Character.list_from_json(data['characters'])
        instance.events = Event.list_from_json(data['events'])
        instance.overall = Overall.from_json(data['overall'])
        return instance

    def to_db(self):
        entity_lists = [
            self.attribs, self.locations, self.items, self.characters,
            self.events]
        for entity_list in entity_lists:
            for entity in entity_list:
                entity.to_db()
        self.overall.to_db()

    @classmethod
    def from_db(cls):
        instance = cls()
        instance.attribs = Attrib.list_from_db()
        instance.locations = Location.list_from_db()
        instance.items = Item.list_from_db()
        instance.characters = Character.list_from_db()
        instance.events = Event.list_from_db()
        instance.overall = Overall.from_db()
        return instance


# Store game data in the g object per user
@app.before_request
def before_request():
    print("before_request()")
    g.game_token = session.get('game_token')
    g.db = db
    g.user_id = session.get(g.game_token, {}).get('user_id')
    if g.user_id:
        g.game_data = GameData.from_db()
        interaction = UserInteraction(g.user_id)
        interaction.to_db()
    else:
        print("no user id and no game data")
        g.game_data = None

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
    user_id = session.get(game_token, {}).get('user_id')
    if not user_id:
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
        user_id = request.form.get('username')
        if user_id:
            # Store the user ID specific to the game token
            session[game_token] = {'user_id': user_id}
            return redirect(url_for('index'))
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
    return render_template(
        'configure/index.html',
        game=g.game_data, current_user_id=g.user_id,
        file_message=file_message)

FILEPATH = 'data/data.json'

@app.route('/save_to_file')
def save_to_file():
    data_to_save = g.game_data.to_json()
    with open(FILEPATH, 'w') as outfile:
        json.dump(data_to_save, outfile, indent=4)
    session['file_message'] = 'Saved to file.'
    return redirect(url_for('configure'))

@app.route('/load_from_file')
def load_from_file():
    with open(FILEPATH, 'r') as infile:
        data = json.load(infile)
        g.game_data = GameData.from_json(data)
    g.game_data.to_db()
    session['file_message'] = 'Loaded from file.'
    return redirect(url_for('configure'))

if __name__ == '__main__':
    app.run()

