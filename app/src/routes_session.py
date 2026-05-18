import uuid
import random
import string
import os
import tempfile
import logging
from datetime import datetime, timedelta
from flask import (
    g, Blueprint, request, session, redirect, url_for, render_template, json,
    current_app, send_file)
from app.models import db, JsonKeys, UserInteraction, Character, Overall
from app.serialization import (
    init_game_session, load_scenario_from_path, DEFAULT_SCENARIO_FILE,
    import_from_dict, patch_from_dict,
    clear_game_data, export_game_to_json, export_to_dict)
from app.utils import RequestHelper, LinkLetters, BaseFieldMap

logger = logging.getLogger(__name__)
session_bp = Blueprint('session', __name__)

# ------------------------------------------------------------------------
# File Handling
# ------------------------------------------------------------------------

COMPLETENESS_LEVELS = [
    "Idea Only",
    "Under Construction",
    "Starter Kit",
    "Has Objectives",
    "Complete",
    "Polished"
]
UNDER_CONSTRUCTION_INDEX = 1

@session_bp.route('/scenarios', methods=['GET', 'POST'])
def browse_scenarios():
    data_dir = current_app.config['DATA_DIR']
    
    if request.method == 'POST':
        req = RequestHelper('form')
        filename = req.get_str('scenario_file')
        if load_scenario_from_path(filename):
            return redirect(url_for('play.overview'))
        return render_template(
            'error.html',
            message="Error loading pre-built scenario.",
            ), HTTPStatus.INTERNAL_SERVER_ERROR

    # GET logic: List files
    scenarios = []
    sort_by = request.args.get('sort_by', 'introduce')

    for filename in os.listdir(data_dir):
        if filename == DEFAULT_SCENARIO_FILE:
            continue
        if filename.endswith('.json'):
            path = os.path.join(data_dir, filename)
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    overall = BaseFieldMap(data.get(JsonKeys.OVERALL, {}))
                    complete = overall.get_str('tag_complete', 'Under Construction')
                    try:
                        complete_rank = COMPLETENESS_LEVELS.index(complete)
                    except ValueError:
                        complete_rank = UNDER_CONSTRUCTION_INDEX
                    scenarios.append({
                        'filename': filename,
                        'title': overall.get_str('title', filename),
                        'description': overall.get_str('description', ''),
                        'introduce': overall.get_int('tag_introduce_order', 50),
                        'best': overall.get_int('tag_best_order', 50),
                        'progress_type': overall.get_str('tag_progress_type', 'Idle'),
                        'multiplayer': overall.get_bool('tag_multiplayer', False),
                        'complete': complete,
                        'complete_rank': complete_rank,
                        'filesize': os.path.getsize(path)
                    })
                except Exception as e:
                    logger.exception(e)

    # Sorting
    if sort_by in ('introduce', 'best', 'title', 'progress_type'):
        reverse = False  # Ascending
    elif sort_by in ('filesize', 'complete_rank', 'multiplayer'):
        reverse = True  # Descending
    else:
        raise ValueError(f"Unexpected sort_by {sort_by}")
    scenarios = sorted(
        scenarios,
        key=lambda x: x.get(sort_by, ''),
        reverse=reverse)
    
    return render_template(
        'configure/scenarios.html', 
        scenarios=scenarios, 
        sort_by=sort_by,
        link_letters=LinkLetters(excluded='om') # Reserve 'o' and 'm' for nav
    )

@session_bp.route('/save')
def save_to_file():
    """Exports the current game token state to a JSON file."""
    json_data = export_game_to_json()
    
    overall = Overall.query.get(g.game_token)
    title = (overall.title or '').strip() or 'scenario'
    filename = f"{title}.json"

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
        tmp.write(json_data)
        path = tmp.name
        
    return send_file(path, as_attachment=True, download_name=filename)

@session_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    """Processes an uploaded JSON and updates the DB."""
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file uploaded", 400
        file = request.files['file']
        json_data = json.load(file)
        try:
            mode = request.form.get('active_mode')
            if mode == 'patch':
                patch_from_dict(json_data)
            else:
                import_from_dict(json_data)
            return redirect(url_for('play.overview'))
        except (SyntaxError, NameError, AttributeError):
            raise
        except Exception as e:
            db.session.rollback()
            logger.exception(e)
            return render_template(
                'error.html',
                message="Couldn't Import",
                details=str(e)
                ), HTTPStatus.INTERNAL_SERVER_ERROR

    return render_template(
        'configure/upload.html', 
    )

@session_bp.route('/clear-all', methods=['POST'])
def clear_all():
    """Wipes data and re-applies the default scenario."""
    clear_game_data()
    init_game_session() 
    return redirect(url_for('play.overview'))

# ------------------------------------------------------------------------
# Utilities for Session Management
# ------------------------------------------------------------------------

def generate_username():
    """Generates a random 10-letter consonant-heavy username."""
    consonants = ''.join(c for c in string.ascii_lowercase if c not in 'aeiouyl')
    return ''.join(random.choice(consonants) for _ in range(10))

def log_user_activity(endpoint, entity_id=None):
    """
    Records a user's presence on a specific route.
    Uses 'ON CONFLICT DO UPDATE' logic via SQLAlchemy.
    """
    if 'username' not in session or not g.game_token:
        return

    # Upsert logic for user interactions
    interaction = UserInteraction.query.filter_by(
        game_token=g.game_token,
        username=session['username'],
        route=endpoint,
        entity_id=str(entity_id) if entity_id else ""
    ).first()

    if interaction:
        interaction.timestamp = db.func.current_timestamp()
    else:
        interaction = UserInteraction(
            game_token=g.game_token,
            username=session['username'],
            route=endpoint,
            entity_id=str(entity_id) if entity_id else ""
        )
        db.session.add(interaction)
    
    try:
        db.session.flush()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to log user interaction: {e}")

# ------------------------------------------------------------------------
# Session Routes
# ------------------------------------------------------------------------

@session_bp.route('/join-game')
def join_game():
    """
    Point of entry via a shared link. Sets the session token 
    and ensures the System Entity (ID 1) exists.
    """
    game_token = request.args.get('game_token')
    if game_token:
        session['game_token'] = game_token
        # Ensure the shared-id registry is bootstrapped for this token
        init_game_session()
        return redirect(url_for('play.overview'))
    
    return "Please include a valid game token in the URL.", 400

@session_bp.route('/session-link')
def get_session_link():
    """Generates the external URL that others can use to join this game."""
    if 'game_token' not in session:
        return "No active session found.", 404
    
    url = url_for('session.join_game', game_token=session['game_token'], _external=True)
    return render_template('session/session_link.html', url=url)

@session_bp.route('/change-user', methods=['GET', 'POST'])
def change_user():
    """Allows the user to set a custom display name."""
    if request.method == 'GET':
        # Provide list of characters as suggestions for names
        characters = Character.query.filter_by(game_token=g.game_token).all()
        return render_template('session/username.html', characters=characters)

    req = RequestHelper('form')
    new_username = req.get_str('username')
    if not new_username:
        new_username = generate_username()
    
    session['username'] = new_username
    
    # Return to the previous page if possible
    referrer = req.get_str('referrer') or url_for('play.overview')
    return redirect(referrer)

@session_bp.route('/session-users')
def session_users():
    """
    Queries the UserInteraction table to show who has 
    been active in the last 5 minutes.
    """
    threshold = datetime.now() - timedelta(minutes=5)
    
    # Get distinct users active recently
    recent_interactions = UserInteraction.query.filter(
        UserInteraction.game_token == g.game_token,
        UserInteraction.timestamp >= threshold
    ).order_by(UserInteraction.timestamp.desc()).all()

    # De-duplicate by username in Python for simpler query logic
    unique_users = {}
    for inter in recent_interactions:
        if inter.username not in unique_users:
            unique_users[inter.username] = inter

    return render_template(
        'session/users.html', 
        interactions=unique_users.values()
    )

# ------------------------------------------------------------------------
# Global App Integration Helper
# ------------------------------------------------------------------------

def init_session_handlers(app):
    """
    Should be called in app.py to register before_request logic.
    """
    @app.before_request
    def ensure_session_data():
        if request.endpoint and request.endpoint.startswith('static'):
            return

        # 1. Ensure Game Token
        if 'game_token' not in session:
            session['game_token'] = str(uuid.uuid4())
        g.game_token = session['game_token']
        
        # 2. Ensure General Storage exists for this session
        init_game_session(g.game_token)

        # 3. Ensure Username
        if 'username' not in session:
            session['username'] = generate_username()
        
        # 4. Log presence (except for administrative/api routes)
        if request.endpoint and not any(x in request.endpoint for x in ['static', 'log_visit']):
            log_user_activity(request.endpoint, request.view_args.get('id') if request.view_args else None)

