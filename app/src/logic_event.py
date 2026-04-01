import random
import math
import logging
from flask import g
from app.models import (
    db, Entity, Event, AttribValue, Pile, Location, Character, Item,
    LocationDest, SourceRole)
from app.src.logic_piles import set_quantity
from app.src.logic_user_interaction import add_message
from app.src.logic_navigation import get_all_valid_coords

logger = logging.getLogger(__name__)

class TriggerException(Exception):
    """Signals that a random event has interrupted normal progress."""
    def __init__(self, message, event_id):
        super().__init__(message)
        self.event_id = event_id
        self.message = message

def check_triggers(entity, batches=1):
    """
    Checks if any events linked to this entity trigger based on probability.
    'batches' represents the number of chances (ticks) that occurred.
    """
    # Find events where this entity_id is listed in 'triggers'
    # In the new model, we need to ensure we have a way to query triggers.
    # For now, let's assume a relationship or a helper query.
    
    # This query finds events that have a trigger_chance > 0 
    # logic depends on how you implement the triggers link table (skipped in previous brief)
    # Assuming Event model has a relationship or we query the registry:
    events = Event.query.filter(
        Event.game_token == g.game_token,
        Event.trigger_chance > 0
    ).all() 

    for event in events:
        # Probability math: 1 - (1 - chance)^batches
        p_failure = 1.0 - event.trigger_chance
        p_overall_success = 1.0 - (p_failure ** batches)
        
        if random.random() < p_overall_success:
            # We have a hit!
            raise TriggerException(f"Encounter: {event.name}", event.id)

# ------------------------------------------------------------------------
# 1. Determinant Logic (Modifiers)
# ------------------------------------------------------------------------

def get_entity_value(owner_id, attrib_id=None, item_id=None):
    """
    Finds the numeric value for a modifier.
    Checks AttribValue or Pile based on what is provided.
    """
    if attrib_id:
        val_obj = AttribValue.query.filter_by(
            game_token=g.game_token, subject_id=owner_id, attrib_id=attrib_id
        ).first()
        return val_obj.value if val_obj else 0.0
    
    if item_id:
        # Sum quantity across all piles (if multiple positions exist in inventory)
        piles = Pile.query.filter_by(
            game_token=g.game_token, owner_id=owner_id, item_id=item_id
        ).all()
        return sum(p.quantity for p in piles)
    
    return 0.0

def resolve_role_id(role, actor_id, target_id, actor_item_id, target_item_id, location_id):
    """Maps a SourceRole to a specific Entity ID."""
    if role == SourceRole.ACTOR: return actor_id
    if role == SourceRole.TARGET: return target_id
    if role == SourceRole.ACTOR_ITEM: return actor_item_id
    if role == SourceRole.TARGET_ITEM: return target_item_id
    if role == SourceRole.LOCATION: return location_id
    if role == SourceRole.GLOBAL: return GENERAL_ID
    return None

def calculate_determinants(event, context_ids):
    """
    Returns a list of calculated modifiers based on selected participants.
    context_ids: {'actor_id', 'target_id', 'actor_item_id', 'target_item_id', 'location_id'}
    """
    modifiers = []
    for det in event.determinants:
        owner_id = resolve_role_id(
            det.source_role, 
            context_ids.get('actor_id'),
            context_ids.get('target_id'),
            context_ids.get('actor_item_id'),
            context_ids.get('target_item_id'),
            context_ids.get('location_id')
        )
        
        if not owner_id:
            continue

        raw_val = get_entity_value(owner_id, det.attrib_id, det.item_id)
        # Apply mode (log, half) logic here
        effective_val = apply_modifier_mode(raw_val, det.mode)
        
        modifiers.append({
            'label': det.label,
            'value': effective_val,
            'op': det.operation
        })
    return modifiers

def apply_modifier_mode(val, mode):
    if mode == 'log':
        if val == 0: return 0
        return math.sign(val) * 5 * math.log10(abs(val) + 1)
    if mode == 'half':
        return val / 2.0
    return val

def apply_modifier(base_val, mod_val, operation, mode):
    """
    Applies logic like 'Soft Capped' (log) or 'Reduced' (half).
    """
    effective_mod = mod_val
    if mode == 'log':
        # scaledLog logic: sign * 5 * log10(abs + 1)
        if effective_mod != 0:
            sign = 1 if effective_mod > 0 else -1
            effective_mod = sign * 5 * (math.log10(abs(effective_mod) + 1))
    elif mode == 'half':
        effective_mod /= 2.0

    if operation == '+': return base_val + effective_mod
    if operation == '-': return base_val - effective_mod
    if operation == '*': return base_val * effective_mod
    if operation == '/': return base_val / effective_mod if effective_mod != 0 else base_val
    return base_val

# ------------------------------------------------------------------------
# 2. Outcome Resolution
# ------------------------------------------------------------------------

def roll_for_outcome(event_id, die_min, die_max, loc_id=None):
    """
    Performs the random roll based on user-provided bounds and Event rules.
    Returns: (numeric_result, string_display)
    """
    event = Event.query.get((g.game_token, event_id))
    die_min = int(round(float(die_min)))
    die_max = int(round(float(die_max)))
    numeric_val = 0.0
    display_str = f""
    
    if event.outcome_type == 'fourway':
        roll = random.randint(die_min, die_max)
        range_size = abs(die_max - die_min) + 1
        crit_threshold = round(range_size * 0.10)
        if roll <= die_min + crit_threshold:
            res = "Strong Failure"
        elif roll <= 0:
            res = "Minor Failure"
        elif roll < die_max - crit_threshold:
            res = "Minor Success"
        else:
            res = "Strong Success"
        numeric_val = float(roll)
        display_str = f"{res} (Rolled {roll})"

    elif event.outcome_type == 'numeric':
        roll = random.randint(die_min, die_max)
        numeric_val = float(roll)
        display_str = f"Rolled {roll}"

    elif event.outcome_type == 'selection':
        options = [s.strip() for s in event.selection_strings.split('\n') if s.strip()]
        choice = random.choice(options) if options else "Nothing"
        numeric_val = 0
        display_str = f"Selection: {choice}"

    elif event.outcome_type == 'coordinates':
        numeric_val, display_str = roll_coordinate(loc_id)

    add_message(g.game_token, f"{event.name} — {display_str}")
    return numeric_val, display_str

def roll_coordinate(loc_id):
    """Pick a random available square at a location."""
    loc = Location.query.get((g.game_token, loc_id))
    if not loc or not loc.dimensions:
        return 0, "No valid grid found for coordinate roll."

    # 2. Get all valid squares (within bounds and not excluded)
    all_valid = set(get_all_valid_coords(loc))
    
    # 3. Identify Occupied Squares
    occupied = set()

    # A. Characters at this location
    chars_here = Character.query.filter_by(
        game_token=g.game_token, location_id=loc.id).all()
    for c in chars_here:
        if c.position:
            occupied.add(tuple(c.position))

    # B. Items on the ground (piles with this loc as owner)
    items_here = Pile.query.filter_by(
        game_token=g.game_token, owner_id=loc.id).all()
    for i in items_here:
        if i.position:
            occupied.add(tuple(i.position))

    # C. Doors/Exits (Destinations)
    dests = LocationDest.query.filter(
        LocationDest.game_token == g.game_token,
        ((LocationDest.loc1_id == loc.id) | (LocationDest.loc2_id == loc.id))
    ).all()
    for d in dests:
        # Add door1 if it's our location, otherwise door2
        door = d.door1 if d.loc1_id == loc.id else d.door2
        if door:
            occupied.add(tuple(door))

    # 4. Subtract occupied from valid
    available = list(all_valid - occupied)
    if not available:
        return 0, "No coordinates available."

    # 5. Pick random and return
    chosen_x, chosen_y = random.choice(available)
    return 0, f"Coordinates: [{chosen_x}, {chosen_y}]"

# ------------------------------------------------------------------------
# 3. Applying Changes
# ------------------------------------------------------------------------

def apply_event_change(target_id, target_type, owner_id, new_value):
    """
    Saves the result of a roll to a specific attribute or item pile.
    - target_id: ID of the Attribute or Item definition.
    - target_type: 'attrib' or 'item'.
    - owner_id: ID of the Character or Location receiving the change.
    - new_value: The final number to be stored.
    """
    if target_type == 'attrib':
        # Find or create the specific stat for this owner
        record = AttribValue.query.filter_by(
            game_token=g.game_token,
            subject_id=owner_id,
            attrib_id=target_id
        ).first()
        if not record:
            record = AttribValue(
                game_token=g.game_token,
                subject_id=owner_id,
                attrib_id=target_id,
                value=0.0)
            db.session.add(record)
        record.value = new_value

    elif target_type == 'item':
        # Find or create the specific inventory pile for this owner
        set_quantity(target_id, owner_id, new_value)

    db.session.commit()
    return True
