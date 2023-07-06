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
    locations = Location.instances
    overall = Overall

game_data = GameData()
Character.game_data = game_data
Item.game_data = game_data
Location.game_data = game_data
Overall.game_data = game_data

@app.route('/')  # route
def index():  # endpoint
    return redirect(url_for('overview'))  # name of endpoint

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

@app.route('/save_to_file')
def save_to_file():
    with open('data.json', 'w') as outfile:
        json.dump(
            Item.instances,
            outfile,
            default=lambda obj: obj.to_json(),
            indent=4)
    session['file_message'] = 'Saved to file.'
    return redirect(url_for('configure'))

@app.route('/load_from_file')
def load_from_file():
    with open('data.json', 'r') as infile:
        data = json.load(infile)
        Item.itemlist_from_json(data)
    session['file_message'] = 'Loaded from file.'
    return redirect(url_for('configure'))

if __name__ == '__main__':
    app.run()

