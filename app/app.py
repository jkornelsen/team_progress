from flask import (
    Flask,
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
    characters = Character.instances
    items = Item.instances
    events = Event.instances
    locations = Location.instances
    overall = Overall

game_data = GameData()
Character.game_data = game_data
Item.game_data = game_data
Event.game_data = game_data
Location.game_data = game_data
Overall.game_data = game_data

@app.route('/')  # route
def index():  # endpoint
    return redirect(url_for('overview'))  # name of endpoint

_set_event_routes(app)
_set_character_routes(app)
_set_item_routes(app)
_set_location_routes(app)
_set_overall_routes(app)

@app.route('/configure')
def configure():
    file_message = session.pop('file_message', False)
    return render_template(
        'configure/index.html',
        game=game_data,
        file_message=file_message)

FILEPATH = 'data/data.json'

@app.route('/save_to_file')
def save_to_file():
    data_to_save = {
        'events': [event.to_json() for event in Event.instances],
        'characters': [character.to_json() for character in Character.instances],
        'items': [item.to_json() for item in Item.instances],
        'locations': [location.to_json() for location in Location.instances],
        'overall': Overall.to_json()
    }
    with open(FILEPATH, 'w') as outfile:
        json.dump(data_to_save, outfile, indent=4)
    session['file_message'] = 'Saved to file.'
    return redirect(url_for('configure'))

@app.route('/load_from_file')
def load_from_file():
    with open(FILEPATH, 'r') as infile:
        data = json.load(infile)
        Overall.from_json(data['overall'])
        Location.list_from_json(data['locations'])
        Item.list_from_json(data['items'])
        Character.list_from_json(data['characters'])
        Event.list_from_json(data['events'])
    session['file_message'] = 'Loaded from file.'
    return redirect(url_for('configure'))

if __name__ == '__main__':
    app.run()

