from datetime import datetime, timedelta, timezone
import logging
import random
import string
from flask import g, session
from sqlalchemy import desc, delete, select, func
from app.models import (
    db, GameMessage, UserInteraction,
    Scenario, IdSequence, Entity, UserInteraction)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# User Tracking
# ------------------------------------------------------------------------

def generate_username():
    """Generates a random 10-letter consonant-heavy username."""
    consonants = ''.join(c for c in string.ascii_lowercase if c not in 'aeiouyl')
    return ''.join(random.choice(consonants) for _ in range(10))

def log_activity(endpoint, entity_id=None):
    """Records a user's presence on a specific route."""
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
        logger.error("Failed to log user interaction: %s", e)

# ------------------------------------------------------------------------
# Game Log
# ------------------------------------------------------------------------

def add_message(text, group_duplicates=True, commit=False):
    """
    Adds a message to the game log.
    If the exact same message was sent recently, increments the count
    instead of spamming the list.
    """
    game_token = g.game_token
    if not text:
        return

    duplicate = False
    if group_duplicates:
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

    db.session.flush()
    if commit:
        db.session.commit()

def get_chronicle(limit=50):
    """Fetches the most recent messages."""
    game_token = g.game_token

    messages = db.session.execute(
        db.select(GameMessage)
        .filter_by(game_token=game_token)
        .order_by(GameMessage.timestamp.desc())
        .limit(limit)
    ).scalars().all()

    # Query gets newest messages; reverse for display order
    messages.reverse()
    return messages

# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------

def clear_session_logs(game_token):
    """
    Permanently deletes all messages and interaction logs for a specific token.
    Called during 'Reset Game' or 'Load Scenario'.
    """
    GameMessage.query.filter_by(game_token=game_token).delete()
    UserInteraction.query.filter_by(game_token=game_token).delete()
    db.session.flush()
    logger.info("Logs cleared for token: %s", game_token)

def clear_old_data(days=1):
    """Maintenance function to delete old messages and user logs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    GameMessage.query.filter(GameMessage.timestamp < cutoff).delete()
    UserInteraction.query.filter(UserInteraction.timestamp < cutoff).delete()
    db.session.commit()

# ------------------------------------------------------------------------
# Maintenance
# ------------------------------------------------------------------------

BOT_ROUTES = ('root', 'main.root')
BOT_HIT_MIN_AGE = timedelta(hours=1)
STALE_TOKEN_AGE = timedelta(days=4)

def purge_bot_hits(now=None):
    """Remove single-hit rows (e.g. from bots) older than an hour."""
    now = now or datetime.now()
    single_hit_users = (
        select(UserInteraction.username)
        .group_by(UserInteraction.username)
        .having(func.count() == 1)
    )
    result = db.session.execute(
        delete(UserInteraction)
        .where(UserInteraction.route.in_(BOT_ROUTES))
        .where(UserInteraction.timestamp < now - BOT_HIT_MIN_AGE)
        .where(UserInteraction.username.in_(single_hit_users))
    )
    return result.rowcount

def find_stale_tokens(now=None):
    """Return game_tokens with no user_interactions within the stale window."""
    now = now or datetime.now()
    last_seen = (
        select(
            UserInteraction.game_token,
            func.max(UserInteraction.timestamp).label('last_ts'),
        )
        .group_by(UserInteraction.game_token)
        .subquery()
    )
    stale = (
        select(Scenario.game_token)
        .outerjoin(last_seen, last_seen.c.game_token == Scenario.game_token)
        .where(
            (last_seen.c.last_ts.is_(None)) |
            (last_seen.c.last_ts < now - STALE_TOKEN_AGE)
        )
    )
    return db.session.execute(stale).scalars().all()

def purge_tokens(tokens):
    """Delete all rows for the given game_tokens across the relational tree."""
    if not tokens:
        return
    db.session.execute(delete(Scenario).where(Scenario.game_token.in_(tokens)))
    db.session.execute(delete(IdSequence).where(IdSequence.game_token.in_(tokens)))
    db.session.execute(delete(Entity).where(Entity.game_token.in_(tokens)))
    db.session.execute(delete(UserInteraction).where(UserInteraction.game_token.in_(tokens)))

def run_purge(now=None):
    """
    Run the full maintenance purge: clear bot noise, then remove any game
    token with no user_interactions inside the stale window.
    Returns the number of game tokens purged.
    """
    now = now or datetime.now()
    purge_bot_hits(now=now)
    tokens = find_stale_tokens(now=now)
    purge_tokens(tokens)
    return len(tokens)

def get_token_statuses(now=None):
    """Return per-token status rows: game_token, title, last_interaction, status."""
    now = now or datetime.now()
    last_seen = (
        select(
            UserInteraction.game_token,
            func.max(UserInteraction.timestamp).label('last_ts'),
        )
        .group_by(UserInteraction.game_token)
        .subquery()
    )
    rows = db.session.execute(
        select(
            Scenario.game_token,
            Scenario.title,
            last_seen.c.last_ts,
        )
        .outerjoin(last_seen, last_seen.c.game_token == Scenario.game_token)
        .order_by(last_seen.c.last_ts.asc().nulls_first())
    ).all()

    result = []
    for game_token, title, last_ts in rows:
        if last_ts is None or last_ts < now - STALE_TOKEN_AGE:
            status = 'inactive'
        else:
            status = 'active'
        result.append({
            'game_token': game_token,
            'title': title,
            'last_interaction': last_ts,
            'status': status,
        })
    return result
