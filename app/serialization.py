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
    Pile, Recipe, RecipeSource, RecipeByproduct, 
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

def patch_from_dict(data):
    """
    Intelligently merges JSON data into the current scenario using an 'ID + Type' 
    fingerprint. 
    
    Behavior:
    1. MATCH: If an incoming Entity ID exists and matches the Type, it updates 
       the existing record (Patch), replacing its collections (piles/exits).
    2. COLLISION: If an ID exists but the Type differs, the incoming entity is 
       treated as new and assigned a generated ID to prevent data corruption.
    3. NEW: If the ID does not exist, it is added as a new entity.
    4. HEURISTIC LINKING: Internal links (within the JSON) are re-mapped to 
       new IDs. External links (pointing to IDs not in the JSON) are 
       preserved if they point to valid existing entities in the database.
    """
    game_token = g.game_token
    overall = Overall.query.filter_by(game_token=game_token).first()
    
    id_map = {}
    entities_to_patch = []
    entities_to_create = []

    entities_data = data.get(JsonKeys.ENTITIES, {})

    # Step 1: Sorting and ID mapping
    for key, model_cls in ENTITIES.items():
        for entry in entities_data.get(key, []):
            old_id = entry.get('id')
            existing = Entity.query.filter_by(game_token=game_token, id=old_id).first()

            if existing and existing.entity_type == key:
                id_map[old_id] = old_id
                entities_to_patch.append((existing, entry))
            else:
                new_id = overall.next_entity_id
                id_map[old_id] = new_id
                overall.next_entity_id += 1
                entities_to_create.append((model_cls, entry))

    # Step 2: Internal Link Resolver
    def resolve(val):
        if not val: return None
        if val in id_map: return id_map[val]
        # Fallback to DB for external links
        exists = Entity.query.filter_by(game_token=game_token, id=val).exists()
        return val if exists else None

    # Step 3: Apply Patches (Updates)
    for db_obj, entry in entities_to_patch:
        # Clear collections to prevent duplicates during merge
        if hasattr(db_obj, 'piles'): db_obj.piles = []
        if hasattr(db_obj, 'exits'): db_obj.exits = []
        
        # Hydrate and merge
        patched = db_obj.__class__.from_dict(entry, game_token)
        db.session.merge(patched)

    # Step 4: Apply Appends (New Entities)
    for model_cls, entry in entities_to_create:
        entry['id'] = id_map[entry['id']]
        # Re-link internal pointers (e.g., location_id or loc2_id)
        # (You'd iterate through fields here to apply the resolve() function)
        
        new_instance = model_cls.from_dict(entry, game_token)
        db.session.add(new_instance)

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
    data = export_to_dict()
    return serialize_smart(data, indent=4, max_line_length=70)

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

def serialize_smart(obj, indent=4, max_line_length=60, current_indent=0):
    """
    Recursively serializes a dict/list to JSON, collapsing small 
    entries onto a single line.
    Makes the file much easier for humans to read/edit.
    """
    padding = " " * current_indent
    inner_padding = " " * (current_indent + indent)

    # --- HANDLE LISTS ---
    if isinstance(obj, list):
        if not obj:
            return "[]"
        
        # Determine if we should collapse the WHOLE list (e.g. [1, 2, 3])
        # We only collapse if it's short AND doesn't contain dictionaries/lists
        items_formatted = [serialize_smart(item, indent, max_line_length, 0) for item in obj]
        flat_list = "[" + ", ".join(items_formatted) + "]"
        
        contains_complex = any(isinstance(item, (dict, list)) for item in obj)
        
        if len(flat_list) <= max_line_length and not contains_complex:
            return flat_list
        
        # Otherwise, vertical list: one item per line, with commas
        lines = []
        for i, item in enumerate(obj):
            comma = "," if i < len(obj) - 1 else ""
            formatted_item = serialize_smart(item, indent, max_line_length, current_indent + indent)
            lines.append(f"\n{inner_padding}{formatted_item}{comma}")
        
        return "[" + "".join(lines) + f"\n{padding}]"

    # --- HANDLE DICTIONARIES ---
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        
        # Try to see if this specific dictionary can be a single line
        # (e.g. {"item_id": 2, "quantity": 5.0})
        items_formatted = [f'"{k}": {serialize_smart(v, indent, max_line_length, 0)}' for k, v in obj.items()]
        flat_dict = "{" + ", ".join(items_formatted) + "}"
        
        # Collapse if it's short and values aren't complex
        contains_complex_val = any(isinstance(v, (dict, list)) for v in obj.values())
        if len(flat_dict) <= max_line_length and not contains_complex_val:
            return flat_dict
            
        # Otherwise, vertical dictionary
        lines = []
        keys = list(obj.keys())
        for i, k in enumerate(keys):
            comma = "," if i < len(keys) - 1 else ""
            val_formatted = serialize_smart(obj[k], indent, max_line_length, current_indent + indent)
            lines.append(f'\n{inner_padding}"{k}": {val_formatted}{comma}')
            
        return "{" + "".join(lines) + f"\n{padding}}}"

    # --- HANDLE PRIMITIVES ---
    return json.dumps(obj)

def range_to_list(r):
    """Converts a Postgres NumericRange to a [min, max] list."""
    if r is None: return [None, None]
    # Handle both infinity and specific values
    lower = r.lower if not r.lower_inf else None
    upper = r.upper if not r.upper_inf else None
    return [lower, upper]

