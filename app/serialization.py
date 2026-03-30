import logging
import json
import os
import re
from flask import g, current_app
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import Range
from .models import (
    GENERAL_ID, ENTITIES, JsonKeys, db,
    Entity, Item, Character, Location, Attrib, Event, 
    Pile, AttribValue, Recipe, RecipeSource, RecipeByproduct, 
    RecipeAttribReq, Progress, LocationDest, Overall, WinRequirement)
from .utils import parse_numrange

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# Load or Reset Model
# ------------------------------------------------------------------------

def init_game_session():
    """Bootstraps a specific game session."""
    game_token = g.game_token
    overall = Overall.query.get(game_token)
    if not overall:
        logger.info(f"Initializing game session")
        success = load_scenario_from_path('00_Default.json')
        if not success:
            logger.warning(f"Falling back to class default.")
            overall = Overall(game_token=game_token)
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

def load_scenario_from_path(filename):
    """
    Helper to load a JSON file from the DATA_DIR and import it.
    """
    path = os.path.join(current_app.config['DATA_DIR'], filename)
    if not os.path.exists(path):
        logger.warning(f"Scenario file not found: {path}")
        return False
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return import_from_dict(data)
    except Exception as e:
        logger.error(f"Failed to load scenario {filename}: {e}")
        return False

def import_from_dict(data):
    """
    Load all data from JSON dictionary.
    Wipes existing session data and rebuilds using model hydration.
    """
    # Wipe current data
    game_token = g.game_token
    db.session.query(Entity).filter_by(game_token=game_token).delete()
    db.session.query(Overall).filter_by(game_token=game_token).delete()
    
    # Overall Settings
    ov = Overall.from_dict(data.get(JsonKeys.OVERALL, {}), game_token)
    db.session.add(ov)
    db.session.add(Entity(
        id=GENERAL_ID, game_token=game_token, name="General Storage",
        entity_type="entity"))

    # Entities
    entities_data = data.get(JsonKeys.ENTITIES, {})
    for key, model_cls in ENTITIES.items():
        for entry in entities_data.get(key, []):
            instance = model_cls.from_dict(entry, game_token)
            db.session.add(instance)
        db.session.commit()

    # General state
    general_data = data.get(JsonKeys.GENERAL, {})
    for pile_data in general_data.get("piles", []):
        pile_data['owner_id'] = GENERAL_ID
        db.session.add(Pile.from_dict(pile_data, game_token))
    for prog_data in general_data.get("progress", []):
        prog_data['host_id'] = GENERAL_ID
        db.session.add(Progress.from_dict(prog_data, game_token))

    # Sync the next ID counter
    db.session.flush()
    max_id = db.session.query(
        func.max(Entity.id)).filter_by(game_token=game_token).scalar()
    ov.next_entity_id = (max_id or 1) + 1
    
    db.session.commit()
    return True

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

# ------------------------------------------------------------------------
# Exporting Model -> JSON
# ------------------------------------------------------------------------

def export_game_to_json():
    """
    Generates a formatted JSON string for file downloads.
    """
    # Get the raw dictionary
    data = export_to_dict()
    
    # Standard JSON stringification
    raw_json = json.dumps(data, indent=4)
    
    # Use the utility to collapse coordinates/ranges onto single lines
    # (Makes the file much easier for humans to read/edit)
    return condense_json(raw_json)

def export_to_dict():
    """
    Serializes the entire game state into a dictionary.
    """
    output = {
        JsonKeys.ENTITIES: {key: [] for key in ENTITIES.keys()},
        JsonKeys.GENERAL: {
            "piles": [],
            "progress": [],
        },
        JsonKeys.OVERALL: {}
    }

    # Overall settings
    game_token = g.game_token
    ov = Overall.query.get(game_token)
    if ov:
        output[JsonKeys.OVERALL] = ov.to_dict()

    # Export all entities
    for key, model_cls in ENTITIES.items():
        entities = model_cls.query.filter(
            model_cls.game_token == game_token
        ).all()
        
        output[JsonKeys.ENTITIES][key] = [ent.to_dict() for ent in entities]

    # General state
    gen_piles = Pile.query.filter_by(game_token=game_token, owner_id=GENERAL_ID).all()
    output[JsonKeys.GENERAL]["piles"] = [
        {"item_id": p.item_id, "quantity": p.quantity} 
        for p in gen_piles
    ]

    gen_progress = Progress.query.filter_by(game_token=game_token, host_id=GENERAL_ID).all()
    output[JsonKeys.GENERAL]["progress"] = [prog.to_dict() for prog in gen_progress]

    return output

def condense_json(json_str):
    """
    Post-processes a JSON string to collapse coordinate-style arrays 
    onto a single line for better manual readability.
    """
    # Collapse any 2-item arrays (e.g., [1, 2] or ["slot", "main"])
    # Patterns: [ value , value ]
    json_str = re.sub(
        r'\[\s*(-?[\d.]+|".*?")\s*,\s*(-?[\d.]+|".*?")\s*\]',
        lambda m: f'[{m.group(1)}, {m.group(2)}]',
        json_str)

    # Specifically target spatial keys that might have more items (like 'excluded' with 4)
    tuple_keys = ["door1", "door2", "dimensions", "excluded", "position", "numeric_range"]
    key_pattern = "|".join(tuple_keys)
    pattern = rf'"({key_pattern})":\s*\[(.*?)\]'

    def collapse_spatial_data(match):
        key = match.group(1)
        # Remove all internal whitespace and newlines
        content = match.group(2).replace("\n", "").replace(" ", "")
        # Re-insert clean spacing: [1,2,3,4] -> [1, 2, 3, 4]
        formatted_content = ", ".join(content.split(","))
        return f'"{key}": [{formatted_content}]'

    # Collapse universal item quantities
    json_str = re.sub(
        r'\{\s*"item_id":\s*(\d+),\s*"quantity":\s*([\d.]+)\s*\}',
        r'{"item_id": \1, "quantity": \2}',
        json_str)

    return re.sub(pattern, collapse_spatial_data, json_str, flags=re.DOTALL)

def range_to_list(r):
    """Converts a Postgres NumericRange to a [min, max] list."""
    if r is None: return [None, None]
    # Handle both infinity and specific values
    lower = r.lower if not r.lower_inf else None
    upper = r.upper if not r.upper_inf else None
    return [lower, upper]

