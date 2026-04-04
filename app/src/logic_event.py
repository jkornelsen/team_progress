import random
import math
import logging
from flask import g
from app.models import (
    db, Entity, Event, Location, Character, Item,
    Operation, OutcomeType, RollerType,
    AttribVal, Pile, LocDest)
from app.src.logic_piles import set_quantity
from app.src.logic_user_interaction import add_message
from app.src.logic_navigation import get_all_valid_coords

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# Determinant Logic (Modifiers)
# ------------------------------------------------------------------------

def get_entity_value(game_token, anchor_id, det):
    """
    The Core Resolver. 
    Handles Base vs Child and Attr vs Qty logic.
    """
    # 1. Determine the Target Entity (The Base or the Selected Child)
    target_id = anchor_id
    
    if det.is_child:
        # If it's a child, we need the specific item instance ID from the request/context
        # This is where 'entity_id=NULL' logic lives in the route.
        target_id = request.form.get(f"{det.source_who}_item_id")
    
    if not target_id and det.source_who != 'univ':
        return 0.0

    # 2. Fetch the Data
    if det.source_mode == 'attr':
        # If entity_id is set, we use that specific Attribute Blueprint
        val_obj = AttribVal.query.filter_by(
            game_token=game_token, subject_id=target_id, attrib_id=det.entity_id
        ).first()
        return val_obj.value if val_obj else 0.0
    
    if det.source_mode == 'qty':
        if det.source_who == 'univ' or not det.is_child:
            # AUTO-FETCH: Sum all piles of the specific item blueprint
            piles = Pile.query.filter_by(
                game_token=game_token, owner_id=target_id, item_id=det.entity_id
            ).all()
            return sum(p.quantity for p in piles)
        else:
            # INSTANCE-FETCH: Get the quantity of the specific selected pile
            pile = Pile.query.get((game_token, target_id))
            return pile.quantity if pile else 0.0

    return 0.0

def resolve_anchor_id(who, context):
    """Maps 'subj', '2nd', '3rd', 'univ' to a physical Entity ID."""
    if who == 'univ': return 1 # General Storage
    return context.get(f"{who}_id")

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

def apply_scaling(val, mode):
    """
    Applies logic like 'Soft Capped' (log) or 'Reduced' (half).
    """
    if mode == 'log':
        if val == 0: return 0
        return math.sign(val) * 5 * math.log10(abs(val) + 1)
    if mode == 'half':
        return val / 2.0
    return val

def apply_modifier(base_val, mod_val, operation, mode):
    """
    Applies mathematical transformation including exponents.
    base_val: The current die bound or calculated outcome.
    mod_val: The value retrieved from the attribute/item.
    """
    # ... existing mode logic (log, half) ...

    if operation == '+': return base_val + mod_val
    if operation == '-': return base_val - mod_val
    if operation == '*': return base_val * mod_val
    if operation == '/': return base_val / mod_val if mod_val != 0 else base_val
    if operation == '^x': return base_val ** mod_val
    if operation == 'x^': return mod_val ** base_val
    return base_val

# ------------------------------------------------------------------------
# Outcome Resolution
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
    dests = LocDest.query.filter(
        LocDest.game_token == g.game_token,
        ((LocDest.loc1_id == loc.id) | (LocDest.loc2_id == loc.id))
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

def roll_for_system_outcome(event_id, num_dice=1, sides=20, bonus=0):
    """
    Handles specific dice systems (D&D, Ironsworn) without
    any database determining factors.
    """
    event = Event.query.get((g.game_token, event_id))
    display_str = ""
    numeric_val = 0.0

    if event.roller_type == RollerType.DND:
        rolls = [random.randint(1, sides) for _ in range(num_dice)]
        total = sum(rolls) + bonus
        
        # d20(🎲18) + 2 formatting from your scales.py
        rolls_details = " + ".join([f"d{sides}(🎲{r})" for r in rolls])
        bonus_str = f" + {bonus}" if bonus != 0 else ""
        
        display_str = f"{rolls_details}{bonus_str} = <b>{total}</b>"
        numeric_val = float(total)

    elif event.roller_type == RollerType.IRONSWORN:
        # Action: d6 + bonus vs two d10s
        action_die = random.randint(1, 6)
        challenge_dice = [random.randint(1, 10), random.randint(1, 10)]
        total = action_die + bonus
        
        hits = sum(1 for die in challenge_dice if total > die)
        res_text = "Strong Hit" if hits == 2 else "Weak Hit" if hits == 1 else "Miss"
        
        display_str = f"Action: {total} (🎲{action_die}+{bonus}) vs [🎲{challenge_dice[0]}][🎲{challenge_dice[1]}] -> <b>{res_text}</b>"
        numeric_val = float(hits) # Use hits as the numeric result for potential triggers

    add_message(g.game_token, f"{event.name}: {display_str}")
    return numeric_val, display_str

    event = Event.query.get((g.game_token, event_id))
    display_str = ""
    numeric_val = 0.0

# ------------------------------------------------------------------------
# Applying Changes
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
        record = AttribVal.query.filter_by(
            game_token=g.game_token,
            subject_id=owner_id,
            attrib_id=target_id
        ).first()
        if not record:
            record = AttribVal(
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

# ------------------------------------------------------------------------
# Triggers
# ------------------------------------------------------------------------

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

