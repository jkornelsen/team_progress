import logging
import json
import os
import re
from flask import g, current_app, session
from sqlalchemy import func, inspect as sa_inspect
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.orm import identity
from .models import (
    GENERAL_ID, ENTITIES, JsonKeys, db,
    Entity, Item, Character, Location, Attrib, Event, 
    Pile, Recipe, RecipeSource, RecipeByproduct, 
    RecipeAttribReq, Progress, Overall, WinRequirement)
from .src.logic_user_interaction import clear_session_logs
from .src.logic_discovery import run_discovery_scan
from app.utils import name_stripped

logger = logging.getLogger(__name__)

original_init = identity.IdentityMap.__init__

def patched_init(self, *args, **kwargs):
    """Catch unexpected JSON dicts in keys."""
    original_init(self, *args, **kwargs)
    original_dict = self._dict
    
    class TrappingDict(dict):
        def __contains__(self, key):
            try:
                return super().__contains__(key)
            except TypeError:
                logger.error(f"Unhashable Identity Key: {repr(key)}")
                raise
    
    trapping = TrappingDict(original_dict)
    self._dict = trapping

identity.IdentityMap.__init__ = patched_init

# ------------------------------------------------------------------------
# Load or Reset Model
# ------------------------------------------------------------------------

DEFAULT_SCENARIO_FILE = "_Default.json"

def init_game_session():
    """Bootstraps a specific game session."""
    game_token = g.game_token
    overall = db.session.get(Overall, game_token)
    if not overall:
        logger.info(f"Initializing game session")
        if not load_scenario_from_path(DEFAULT_SCENARIO_FILE):
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
        logger.exception(e)
        return False

def import_from_dict(data):
    """
    Load all data from JSON dictionary.
    Wipes existing session data and rebuilds using model hydration.
    """
    # Wipe current data
    game_token = g.game_token
    clear_game_data()
    for key in ['old_char_id', 'old_loc_id', 'default_slot']:
        session.pop(key, None)
    
    # Overall Settings
    ov_data = data.get(JsonKeys.OVERALL, {})
    ov = Overall.from_dict(ov_data, game_token)
    db.session.add(ov)
    db.session.add(Entity(
        id=GENERAL_ID, game_token=game_token, name="General Storage",
        entity_type="entity"))

    # Entities
    entities_data = data.get(JsonKeys.ENTITIES, {})
    entities_data = remap_general_id(entities_data)
    for key, model_cls in ENTITIES.items():
        for entry in entities_data.get(key, []):
            instance = model_cls.from_dict(entry, game_token)
            db.session.add(instance)
    general_data = data.get(JsonKeys.GENERAL, {})
    for pile_data in general_data.get("piles", []):
        db.session.add(Pile.from_dict(pile_data, game_token, GENERAL_ID))

    # State
    for prog_data in data.get("progress", []):
        db.session.add(Progress.from_dict(prog_data, game_token))

    # Sync the next ID counter
    db.session.flush()
    max_id = db.session.query(
        func.max(Entity.id)).filter_by(game_token=game_token).scalar()
    ov.next_entity_id = (max_id or 1) + 1
    
    db.session.commit()

    # Check for unmasking dependencies
    run_discovery_scan(game_token)

    return True

def remap_general_id(entities_data):
    """
    If any user-provided entity uses GENERAL_ID, remap it to a new unique ID
    and update all internal references within the entities_data.
    """
    # 1. Check for the specific conflict
    conflict_found = False
    max_id = GENERAL_ID
    
    for category in entities_data.values():
        for entry in category:
            current_id = entry.get('id', 0)
            if current_id == GENERAL_ID:
                conflict_found = True
            if current_id > max_id:
                max_id = current_id

    if not conflict_found:
        return entities_data

    # 2. Perform the swap
    new_id = max_id + 1
    
    def walk_and_remap(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                # Remap the actual ID or any FK pointing to it
                # Exclude owner/host because they point to the SYSTEM general storage
                if k == 'id' or (k.endswith('_id') and k not in ['owner_id', 'host_id']):
                    if v == GENERAL_ID:
                        obj[k] = new_id
                else:
                    walk_and_remap(v)
        elif isinstance(obj, list):
            for item in obj:
                walk_and_remap(item)

    walk_and_remap(entities_data)
    return entities_data

def patch_from_dict(data):
    """
    Intelligently merges JSON data into the current scenario.
    
    HOW IT WORKS:
    1.  ID MAPPING (Phase 1):
        We iterate through every entity in the JSON. 
        - If an ID exists in the DB AND the Entity Type matches (e.g., both are Items), 
          we keep the ID (Update Mode).
        - If the ID is missing OR the Type differs (e.g., JSON says ID 5 is an Item, 
          but DB says ID 5 is a Character), we assign a NEW unique ID (Append Mode).
          
    2.  LINK RESOLUTION (Phase 2):
        Because we might have changed IDs in Phase 1, any internal references in 
        the JSON (like a Character's 'location_id' or a Recipe's 'product_id') 
        are now broken. We recursively walk through the JSON data and replace 
        old IDs with the newly mapped IDs from Phase 1.
        
    3.  STATE MERGING (Phase 3):
        - For NEW entities: We hydrate them via .from_dict() and add to session.
        - For EXISTING entities: We clear their current collections (piles, 
          attrib_values, etc.) to prevent the merge from duplicating rows, 
          then use SQLAlchemy's merge() to update all fields.
    """
    game_token = g.game_token
    overall = db.session.get(Overall, game_token)
    
    # --- PHASE 0: DETERMINE NEXT ID ---
    # Find the absolute highest ID currently in use in the DB
    max_db = db.session.query(
        db.func.max(Entity.id)).filter_by(game_token=game_token).scalar() or 0
    
    # Find the highest ID mentioned in the incoming JSON
    json_ids = []
    entities_data = data.get(JsonKeys.ENTITIES, {})
    for key in ENTITIES:
        for entry in entities_data.get(key, []):
            if 'id' in entry: json_ids.append(entry['id'])
    max_json = max(json_ids) if json_ids else 0
    logger.debug(f"Max DB ID: {max_db} | Max JSON ID: {max_json}")
    
    # Force the counter to be higher than BOTH
    overall.next_entity_id = max(overall.next_entity_id, max_db, max_json) + 1
    db.session.flush()

    # --- PHASE 1: ID MAPPING ---
    id_map = {}
    entities_to_process = [] 

    for key, model_cls in ENTITIES.items():
        type_str = model_cls.__mapper_args__['polymorphic_identity']
        for entry in entities_data.get(key, []):
            old_id = entry.get('id')
            existing = Entity.query.filter_by(
                game_token=game_token, id=old_id).order_by(
                name_stripped()).first()

            # If type matches, update existing (ID stays same)
            if existing and existing.entity_type == type_str:
                id_map[old_id] = old_id
                entities_to_process.append((model_cls, entry, False))
            else:
                # Collision or New: Generate a fresh ID (Guaranteed > max_db)
                new_id = Overall.generate_next_id(game_token)
                id_map[old_id] = new_id
                entities_to_process.append((model_cls, entry, True))

    # --- PHASE 2: LINK RESOLUTION ---
    def resolve_links(node):
        if isinstance(node, list):
            for item in node: resolve_links(item)
        elif isinstance(node, tuple): # Add tuple support for Phase 3 list
            for item in node: resolve_links(item)
        elif isinstance(node, dict):
            links = ['id', 'location_id', 'product_id', 'recipe_id', 'item_id', 
                     'attrib_id', 'subject_id', 'loc1_id', 'loc2_id', 'target_id']
            for key in links:
                if key in node and node[key] in id_map:
                    node[key] = id_map[node[key]]
            for val in node.values():
                if isinstance(val, (dict, list)): resolve_links(val)

    resolve_links(entities_to_process)

    # --- PHASE 3: EXECUTION ---
    for model_cls, entry, is_new in entities_to_process:
        # Note: entry['id'] is already updated by resolve_links
        if is_new:
            db.session.add(model_cls.from_dict(entry, game_token))
        else:
            existing_obj = db.session.get(model_cls, (game_token, entry['id']))
            # Wipe collections to prevent data stacking
            for attr in ['piles', 'recipes', 'attrib_values', 'routes_forward', 'item_refs']:
                if hasattr(existing_obj, attr): setattr(existing_obj, attr, [])
            
            db.session.merge(model_cls.from_dict(entry, game_token))

    db.session.commit()
    return True

def clear_game_data(game_token=None):
    """Wipes all data associated with a specific token."""
    game_token = game_token or g.game_token
    logger.info(f"Clearing all data for token: {game_token}")
    
    # Due to CASCADE constraints, these two lines wipe the entire relational tree
    Overall.query.filter_by(game_token=game_token).delete()
    Entity.query.filter_by(game_token=game_token).delete()
    clear_session_logs(game_token)

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
        JsonKeys.OVERALL: {},
        JsonKeys.ENTITIES: {
            key: [] for key in ENTITIES.keys()
        },
        JsonKeys.GENERAL: {
            "piles": []
        },
        "progress": []
    }

    # Overall settings
    game_token = g.game_token
    ov = db.session.get(Overall, game_token)
    if ov:
        output[JsonKeys.OVERALL] = ov.to_dict()

    # Entities
    for key, model_cls in ENTITIES.items():
        entities = model_cls.query.filter(
            model_cls.game_token == game_token
        ).order_by(name_stripped()).all()
        
        output[JsonKeys.ENTITIES][key] = [ent.to_dict() for ent in entities]

    gen_piles = Pile.query.filter_by(
        game_token=game_token, owner_id=GENERAL_ID).all()
    output[JsonKeys.GENERAL]["piles"] = [
        p.to_dict()
        for p in sorted(gen_piles, key=lambda p: (p.item_id, p.quantity))
    ]

    # State
    progress_rows = Progress.query.filter_by(game_token=game_token).all()
    output["progress"] = [
        row.to_dict()
        for row in sorted(progress_rows, key=lambda p: p.recipe_id)
    ]

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
        items_formatted = [
            serialize_smart(item, indent, max_line_length, 0) for item in obj]
        flat_list = "[" + ", ".join(items_formatted) + "]"
        
        contains_complex = any(isinstance(item, (dict, list)) for item in obj)
        
        if len(flat_list) <= max_line_length and not contains_complex:
            return flat_list
        
        # Otherwise, vertical list: one item per line, with commas
        lines = []
        for i, item in enumerate(obj):
            comma = "," if i < len(obj) - 1 else ""
            formatted_item = serialize_smart(
                item, indent, max_line_length, current_indent + indent)
            lines.append(f"\n{inner_padding}{formatted_item}{comma}")
        
        return "[" + "".join(lines) + f"\n{padding}]"

    # --- HANDLE DICTIONARIES ---
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        
        # Try to see if this specific dictionary can be a single line
        # (e.g. {"item_id": 2, "quantity": 5.0})
        items_formatted = [
            f'"{k}": {serialize_smart(v, indent, max_line_length, 0)}'
            for k, v in obj.items()]
        flat_dict = "{" + ", ".join(items_formatted) + "}"
        
        # Collapse if it's short and values aren't complex
        contains_complex_val = any(
            isinstance(v, (dict, list)) for v in obj.values())
        if len(flat_dict) <= max_line_length and not contains_complex_val:
            return flat_dict
            
        # Otherwise, vertical dictionary
        lines = []
        keys = list(obj.keys())
        for i, k in enumerate(keys):
            comma = "," if i < len(keys) - 1 else ""
            val_formatted = serialize_smart(
                obj[k], indent, max_line_length, current_indent + indent)
            lines.append(f'\n{inner_padding}"{k}": {val_formatted}{comma}')
            
        return "{" + "".join(lines) + f"\n{padding}}}"

    # --- HANDLE PRIMITIVES ---
    # Prefer unicode symbols over escape sequences as they're easier to edit.
    return json.dumps(obj, ensure_ascii=False)

def range_to_list(r):
    """Converts a Postgres NumericRange to a [min, max] list."""
    if r is None: return [None, None]
    # Handle both infinity and specific values
    lower = r.lower if not r.lower_inf else None
    upper = r.upper if not r.upper_inf else None
    return [lower, upper]

# ------------------------------------------------------------------------
# Model Changes
# ------------------------------------------------------------------------

def increment_name(name):
    """Adds ' 2' or increments a trailing number for duplicates."""
    match = re.search(r'(.*?)(\d*)$', name.strip())
    base, num = match.groups()
    if num:
        return f"{base.rstrip()}{int(num) + 1}"
    return f"{name} 2"

def clone_entity(source_id, entity_type):
    """
    Creates a deep copy of an entity and its related records.
    Does NOT commit the session, allowing callers to modify 
    the object before saving.
    """
    game_token = g.game_token
    model_class = ENTITIES[f"{entity_type}s"]
    src = db.session.get(model_class, (game_token, source_id))
    if not src:
        return None
    
    data = src.to_dict()

    # Recursive helper to strip 'id' so new IDs are generated
    def strip_ids(obj):
        if isinstance(obj, dict):
            obj.pop('id', None)
            for v in obj.values(): strip_ids(v)
        elif isinstance(obj, list):
            for item in obj: strip_ids(item)

    strip_ids(data)
    data['name'] = increment_name(src.name)
    
    # Re-hydrate into a new instance
    new_obj = model_class.from_dict(data, game_token)
    db.session.add(new_obj)
    return new_obj

