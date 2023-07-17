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
from pymongo import MongoClient
import uuid

from src.game_data import GameData
from src.user_interaction import UserInteraction

from src.attrib    import set_routes as _set_routes_attrib
from src.character import set_routes as _set_routes_character
from src.event     import set_routes as _set_routes_event
from src.item      import set_routes as _set_routes_item
from src.location  import set_routes as _set_routes_location
from src.overall   import set_routes as _set_routes_overall
from src.file      import set_routes as _set_routes_file

app = Flask(__name__)
app.config['SECRET_KEY'] = 'team-adventurers'
app.config['TITLE'] = 'Team Adventurers'
app.config['TEMPLATES_AUTO_RELOAD'] = True  # set to False for production
app.config['MONGO_URI'] = 'mongodb://localhost:27017/team_adventurers_db'
client = MongoClient(app.config['MONGO_URI'])
db = client['team_adventurers_db']

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

_set_routes_attrib(app)
_set_routes_character(app)
_set_routes_event(app)
_set_routes_item(app)
_set_routes_location(app)
_set_routes_overall(app)
_set_routes_file(app)

@app.route('/configure')
def configure():
    file_message = session.pop('file_message', False)
    return render_template(
        'configure/index.html',
        game=g.game_data, current_user_id=g.user_id,
        file_message=file_message)

if __name__ == '__main__':
    app.run()

