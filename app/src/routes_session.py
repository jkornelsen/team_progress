import uuid
import random
import string
import logging
from datetime import datetime, timedelta
from flask import Blueprint, request, session, redirect, url_for, render_template, g
from app.models import db, UserInteraction, Character
from app.serialization import init_game_session

logger = logging.getLogger(__name__)
session_bp = Blueprint('session', __name__)

# ------------------------------------------------------------------------
# 1. Utilities for Session Management
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
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to log user interaction: {e}")

# ------------------------------------------------------------------------
# 2. Routes
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
        init_game_session(game_token)
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
# 3. Global App Integration Helper
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
