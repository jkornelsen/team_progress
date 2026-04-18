import logging
from flask import g
from datetime import datetime, timedelta, timezone
from sqlalchemy import desc
from app.models import db, GameMessage, UserInteraction, Entity

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# 1. Chronicle (Game Message Log)
# ------------------------------------------------------------------------

def add_message(text):
    """
    Adds a message to the game log. 
    If the exact same message was sent recently, increments the count 
    instead of spamming the list.
    """
    game_token = g.game_token
    if not text:
        return

    # 1. Check for a very recent duplicate (within the last 2 minutes)
    recent_threshold = datetime.now(timezone.utc) - timedelta(minutes=2)
    
    duplicate = GameMessage.query.filter(
        GameMessage.game_token == game_token,
        GameMessage.message == text,
        GameMessage.timestamp >= recent_threshold
    ).order_by(desc(GameMessage.timestamp)).first()

    if duplicate:
        duplicate.count += 1
        duplicate.timestamp = datetime.now(timezone.utc) # Refresh time
    else:
        # 2. Create new message
        msg = GameMessage(
            game_token=game_token,
            message=text,
            count=1
        )
        db.session.add(msg)
    
    # 3. Occasional pruning: keep only last 100 messages for this token
    # (Simplified: in a high-traffic app, move to a background task)
    db.session.commit()

def get_chronicle(game_token, limit=50):
    """Fetches the most recent messages for the UI."""
    return GameMessage.query.filter_by(game_token=game_token)\
        .order_by(desc(GameMessage.timestamp))\
        .limit(limit).all()

# ------------------------------------------------------------------------
# 2. Presence Tracking
# ------------------------------------------------------------------------

def log_activity(game_token, username, route, entity_id=None):
    """
    Records which page a user is viewing. 
    Uses an UPSERT-style pattern to keep one record per user per unique view.
    """
    # Normalize entity_id to string as stored in DB
    eid_str = str(entity_id) if entity_id else ""
    
    interaction = UserInteraction.query.filter_by(
        game_token=game_token,
        username=username,
        route=route,
        entity_id=eid_str
    ).first()

    if interaction:
        interaction.timestamp = datetime.now(timezone.utc)
    else:
        interaction = UserInteraction(
            game_token=game_token,
            username=username,
            route=route,
            entity_id=eid_str
        )
        db.session.add(interaction)
    
    db.session.commit()

def get_active_sessions(game_token, minutes=5):
    """
    Returns a list of unique users active within the threshold,
    along with a human-readable description of what they are doing.
    """
    threshold = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    
    # Get all recent interactions
    recent = UserInteraction.query.filter(
        UserInteraction.game_token == game_token,
        UserInteraction.timestamp >= threshold
    ).order_by(desc(UserInteraction.timestamp)).all()

    # De-duplicate by username in Python (keep the latest interaction)
    results = []
    seen_users = set()
    
    for inter in recent:
        if inter.username not in seen_users:
            # Add a dynamic 'action_display' for the UI
            inter.display_action = format_action_string(game_token, inter)
            results.append(inter)
            seen_users.add(inter.username)
            
    return results

# ------------------------------------------------------------------------
# 3. Helpers
# ------------------------------------------------------------------------

def format_action_string(game_token, interaction):
    """
    Converts a route and entity ID into a friendly string.
    Example: 'play.play_character' + '5' -> 'Viewing Character: Valerius'
    """
    endpoint = interaction.route
    eid = interaction.entity_id
    
    if 'overview' in endpoint: return "Viewing Overview"
    if 'configure' in endpoint: return "In Main Setup"
    
    # If we have an entity ID, try to get its name from our registry
    if eid and eid.isdigit():
        entity = Entity.query.get((int(eid), game_token))
        if entity:
            type_label = entity.entity_type.capitalize()
            return f"Viewing {type_label}: {entity.name}"
            
    return "Exploring"

def clear_session_logs(game_token):
    """
    Permanently deletes all messages and interaction logs for a specific token.
    Called during 'Reset Game' or 'Load Scenario'.
    """
    GameMessage.query.filter_by(game_token=game_token).delete()
    UserInteraction.query.filter_by(game_token=game_token).delete()
    db.session.commit()
    logger.info(f"Logs cleared for token: {game_token}")

def clear_old_data(days=1):
    """Maintenance function to delete old messages and user logs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    GameMessage.query.filter(GameMessage.timestamp < cutoff).delete()
    UserInteraction.query.filter(UserInteraction.timestamp < cutoff).delete()
    db.session.commit()
