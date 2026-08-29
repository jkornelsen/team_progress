from datetime import datetime, timedelta, timezone
import logging
import random
import string
import uuid
from flask import g, session
from sqlalchemy import desc, delete, select, func, and_
from app.models import (
    db, GameMessage, UserInteraction, Scenario, IdSequence, Entity)

logger = logging.getLogger(__name__)

STALE_TOKEN_AGE = timedelta(days=4)
ENABLE_LOG_PRUNING = False

# ------------------------------------------------------------------------
# User Tracking
# ------------------------------------------------------------------------

def generate_username():
    """Generates a random 10-letter consonant-heavy username."""
    consonants = ''.join(c for c in string.ascii_lowercase if c not in 'aeiouyl')
    return ''.join(random.choice(consonants) for _ in range(10))

def new_game_token():
    """Generates a fresh, unique game token string."""
    return str(uuid.uuid4())

def log_activity(endpoint, entity_id=None):
    """Records a user's presence on a specific route."""
    if 'username' not in session or not g.game_token:
        return

    scenario = db.session.get(Scenario, g.game_token)

    # Upsert logic for user interactions
    interaction = UserInteraction.query.filter_by(
        game_token=g.game_token,
        username=session['username'],
        route=endpoint,
        entity_id=str(entity_id) if entity_id else ""
    ).first()

    if interaction:
        interaction.timestamp = db.func.current_timestamp()
        interaction.title = scenario.title
    else:
        interaction = UserInteraction(
            game_token=g.game_token,
            username=session['username'],
            title=scenario.title,
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

def clear_old_data(days=7):
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
    if ENABLE_LOG_PRUNING:
        clear_old_data()
    tokens = find_stale_tokens(now=now)
    purge_tokens(tokens)
    return len(tokens)

def get_token_statuses(now=None):
    """Return per-token status rows: game_token, title, last_interaction, status."""
    now = now or datetime.now()

    # Subquery: Get the latest interaction for every token.
    # Use ROW_NUMBER() to ensure we get the specific title 
    # associated with the most recent timestamp.
    latest_hit_subq = (
        select(
            UserInteraction.game_token,
            UserInteraction.title,
            UserInteraction.timestamp,
            func.row_number().over(
                partition_by=UserInteraction.game_token,
                order_by=UserInteraction.timestamp.desc()
            ).label('rn')
        )
        .subquery()
    )

    # Main Query: Start with Scenario to ensure we see every token.
    # Join to the subquery to find the "Top 1" interaction.
    stmt = (
        select(
            Scenario.game_token,
            # Captured title is preferred; Scenario title is the fallback
            func.coalesce(
                latest_hit_subq.c.title, Scenario.title).label('final_title'),
            latest_hit_subq.c.timestamp
        )
        .outerjoin(latest_hit_subq, and_(
            Scenario.game_token == latest_hit_subq.c.game_token,
            latest_hit_subq.c.rn == 1  # Only pick the latest interaction row
        ))
        .order_by(latest_hit_subq.c.timestamp.asc().nulls_first())
    )

    rows = db.session.execute(stmt).all()

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
