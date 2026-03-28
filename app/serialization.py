import logging
import json
from flask import g
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import Range
from .models import (
    db, Entity, Item, Character, Location, Attrib, Event, 
    Pile, AttribValue, Recipe, RecipeSource, RecipeByproduct, 
    RecipeAttribReq, Progress, LocationDest, Overall, WinRequirement,
    GENERAL_ID
)
from .utils import parse_numrange

logger = logging.getLogger(__name__)

ENTITIES = {
    'items': Item,
    'locations': Location,
    'characters': Character,
    'attribs': Attrib,
    'events': Event
}

# ------------------------------------------------------------------------
# Exporting Model -> JSON
# ------------------------------------------------------------------------

def export_to_dict():
    """
    Serializes the entire game state into a dictionary.
    """
    output = { key: [] for key in ENTITIES.keys() }
    output["overall"] = {}

    # Overall settings
    game_token = g.game_token
    ov = Overall.query.get(game_token)
    if ov:
        output["overall"] = ov.to_dict()

    # Export all entities
    for key, model_cls in ENTITIES.items():
        entities = model_cls.query.filter(
            model_cls.game_token == game_token
        ).all()
        
        output[key] = [ent.to_dict() for ent in entities]

    return output

def export_game_to_json():
    """
    Generates a formatted JSON string for file downloads.
    Calls export_to_dict and applies 'condense_json' for readability.
    """
    # Get the raw dictionary
    data = export_to_dict()
    
    # Standard JSON stringification
    raw_json = json.dumps(data, indent=4)
    
    # Use the utility to collapse coordinates/ranges onto single lines
    # (Makes the file much easier for humans to read/edit)
    return condense_json(raw_json)

def range_to_list(r):
    """Converts a Postgres NumericRange to a [min, max] list."""
    if r is None: return [None, None]
    # Handle both infinity and specific values
    lower = r.lower if not r.lower_inf else None
    upper = r.upper if not r.upper_inf else None
    return [lower, upper]

# ------------------------------------------------------------------------
# Importing JSON -> Model
# ------------------------------------------------------------------------

def import_from_dict(data):
    """
    Load all data from JSON dictionary.
    Wipes existing session data and rebuilds using model hydration.
    """
    # 1. Wipe current data
    game_token = g.game_token
    db.session.query(Entity).filter_by(game_token=game_token).delete()
    db.session.query(Overall).filter_by(game_token=game_token).delete()
    
    # 2. Overall Settings
    ov = Overall.from_dict(data.get('overall', {}), game_token)
    db.session.add(ov)
    db.session.add(Entity(
        id=GENERAL_ID, game_token=game_token, name="General Storage",
        entity_type="entity"))

    # 3. Entities
    for key, model_cls in ENTITIES.items():
        for entry in data.get(key, []):
            instance = model_cls.from_dict(entry, game_token)
            db.session.add(instance)

    # 4. Sync the next ID counter
    db.session.flush()
    max_id = db.session.query(func.max(Entity.id)).filter_by(game_token=game_token).scalar()
    ov.next_entity_id = (max_id or 1) + 1
    
    db.session.commit()
    return True

# ------------------------------------------------------------------------
# Reset Scenario in Database
# ------------------------------------------------------------------------

def init_game_session(title="New Game"):
    """Bootstraps a specific game session."""
    game_token = g.game_token
    overall = Overall.query.get(game_token)
    if not overall:
        logger.info(f"Initializing game session: {game_token}")
        overall = Overall(
            game_token=game_token,
            title=title,
            description="A new adventure begins.",
            progress_type="Idle",
            number_format="en_US"
        )
        db.session.add(overall)

        # Ensure the 'General Storage' owner exists
        reserved_entity = Entity.query.filter_by(
            id=GENERAL_ID, game_token=game_token).first()
        if not reserved_entity:
            reserved_entity = Entity(
                id=GENERAL_ID,
                game_token=game_token,
                name="General Storage",
                description="Reserved entity for universal items."
            )
            db.session.add(reserved_entity)
            
        db.session.commit()
    else:
        logger.debug(f"Session {game_token} already initialized.")

def clear_game_data():
    """
    Wipes all data associated with a specific token.
    Useful for 'Reset Game' or 'Blank Scenario' functionality.
    """
    game_token = g.game_token
    logger.warning(f"Clearing all data for token: {game_token}")
    
    # Due to CASCADE constraints, these two lines wipe the entire relational tree
    Entity.query.filter_by(game_token=game_token).delete()
    Overall.query.filter_by(game_token=game_token).delete()
    
    db.session.commit()
    logger.info(f"Token {game_token} cleared.")

