import random
import math
import logging
from flask import g, request, session
from app.models import (
    db, GENERAL_ID, Entity, Item, Location, Character, Attrib, Event,
    StorageType, Operation, OutcomeType, RollerType, Participant,
    AttribVal, Pile, LocDest)
from app.src.logic_piles import set_quantity
from app.src.logic_user_interaction import add_message
from app.src.logic_navigation import get_all_valid_coords

logger = logging.getLogger(__name__)

def format_for_display(val):
    """Round and strip trailing 0's"""
    return f"{round(val, 2):g}"

# ------------------------------------------------------------------------
# Determinant Logic (Modifiers)
# ------------------------------------------------------------------------

def get_entity_value(anchor_id, field_def):
    """Handles Attr vs Qty and Base vs Child."""
    game_token=g.game_token
    
    if field_def.field_mode == Participant.ATTR:
        # 1. Determine the Target Entity (The Base or the Selected Child)
        # TODO: if field_def.child_of_anchor then search entities
        # owned by anchor_id
        target_id = anchor_id

        # If entity_id is set, we use that specific Attribute Blueprint
        val_obj = AttribVal.query.filter_by(
            game_token=game_token,
            subject_id=target_id,
            attrib_id=field_def.attrib_id
        ).first()
        return val_obj.value if val_obj else 0.0
    
    anchor_ent = Entity.query.get((game_token, anchor_id))
    is_item_anchor = anchor_ent and anchor_ent.entity_type == Item.TYPENAME

    if field_def.field_mode == Participant.QTY and field_def.item_id:
        item = Item.query.get((game_token, field_def.item_id))
        if item and item.storage_type == StorageType.UNIVERSAL:
            owner_id = GENERAL_ID
        elif is_item_anchor:
            owner_id = session.get('old_char_id') or session.get('old_loc_id') or GENERAL_ID
        else:
            owner_id = anchor_id

        #TODO: limit by position
        pile = Pile.query.filter_by(
            game_token=game_token,
            owner_id=owner_id,
            item_id=field_def.item_id
        ).first()
        return pile.quantity if pile else 0.0

    return 0.0

def resolve_anchor_id(role_name, role_entities):
    """
    Maps a named participant slot to an entity ID.
    This entity ID comes from URL parameters, or from selectbox,
    or a single value returned by lookup that meets the requirements.

    role_name: e.g. '[Subject]' or 'Target'
    role_entities: e.g. {'[Subject]': 17, 'Target': 18}
    """
    return role_entities.get(role_name)

def calculate_determinants(event, role_entities):
    """
    Returns a list of calculated modifiers based on selected participants.
    """
    modifiers = []
    game_token = g.game_token

    field_name = "Value"
    source_display = "(Constant)"
    for det in event.determinants:
        val = 0.0
        source_display = "Constant"
            
        # Identify the Field Name (Pathfinding, Iron Ore, etc.)
        if det.val_src == 'field' and det.infield:
            anchor_id = resolve_anchor_id(det.infield.role, role_entities)
            if not anchor_id:
                continue
            val = get_entity_value(anchor_id, det.infield)
            infield = det.infield

            if infield.field_mode == Participant.ATTR:
                attr = Attrib.query.get((game_token, infield.attrib_id))
                field_name = attr.name if attr else "Attribute"
            elif infield.field_mode == Participant.QTY:
                item = Item.query.get((game_token, infield.item_id))
                field_name = item.name if item else "Quantity"

            anchor = Entity.query.get((game_token, anchor_id))
            anchor_name = '' if anchor_id == GENERAL_ID \
                else anchor.name if anchor else "Unknown"
            source_display = anchor_name
            if infield.child_of_anchor:
                # If looking at an item instance inside Suzy, we need the item name
                instance_id = role_entities.get(f"{infield.role}_item_id")
                if instance_id:
                    pile = Pile.query.get((game_token, instance_id))
                    if pile:
                        source_display = f"{anchor_name}'s {pile.item.name}"
                else:
                    source_display = f"{anchor_name}'s Item"
        elif det.val_src == 'const':
            val = det.val_transform

        breakdown_text = format_for_display(val)

        # Inner Transform: (Val <op> Constant)
        if det.op_transform:
            breakdown_text = get_inner_breakdown(
                val, det.val_transform, det.op_transform)
            val = apply_operation(
                val, det.val_transform, det.op_transform)

        # Assemble the enriched dictionary
        modifiers.append({
            'label': det.label or "",
            'source_name': source_display,
            'field_name': field_name,
            'value': val,
            'breakdown': breakdown_text,
            'op': det.op_application
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

def apply_operation(current_val, mod_val, op):
    """Applies the specific operation and returns the new value."""
    if op == '+': return current_val + mod_val
    if op == '-': return current_val - mod_val
    if op == '*': return current_val * mod_val
    if op == '/': return current_val / mod_val if mod_val != 0 else current_val
    if op == 'x^': return current_val ** mod_val  # Val to Power (xⁿ)
    if op == '^x': return mod_val ** current_val  # Power of Val (nˣ)
    return current_val

def get_inner_breakdown(val, mod_val, op):
    """Formats the inner transformation for the UI."""
    v = format_for_display(val)
    m = format_for_display(mod_val)
    formats = {
        'c':    m,
        '+':    f"({v}+{m})",
        '-':    f"({v}-{m})",
        '*':    f"({v}×{m})",
        '/':    f"({v}÷{m})",
        'x^':   f"{v}<sup>{m}</sup>",
        '^x':   f"{m}<sup>{v}</sup>",
        'log':  f"log({v})",
        'sqrt': f"√{v}",
        '0.5':  f"{v}/2"
    }
    return formats.get(op, v)

# ------------------------------------------------------------------------
# Outcome Resolution
# ------------------------------------------------------------------------

def roll_for_outcome(event_id, role_entities, difficulty=0.0):
    """
    Performs the random roll based on user-provided difficulty and Event rules.
    Returns: (numeric_result, string_display)
    """
    game_token = g.game_token
    event = Event.query.get((g.game_token, event_id))
    
    # 1. Start with the Base Roll
    base_min = event.numeric_range[0] if event.numeric_range else 1
    base_max = event.numeric_range[1] if event.numeric_range else 20

    # "Determined" events don't roll; they use the single_number as the start.
    if event.outcome_type == 'determined':
        total = event.single_number
        breakdown_parts = [format_for_display(total)]

    elif event.outcome_type == 'selection':
        options = [s.strip() for s in event.selection_strings.split('\n') if s.strip()]
        choice = random.choice(options) if options else "Nothing"
        breakdown_parts = [f"Selection: <b>{choice}</b>"]

    elif event.outcome_type == 'coordinates':
        loc_id = role_entities.get(Participant.SUBJECT)
        _, coord_str = roll_coordinate(loc_id)
        breakdown_parts = [coord_str]

    else:
        die_roll = random.randint(base_min, base_max)
        total = float(die_roll)
        breakdown_parts = [f"d{base_max - base_min + 1}(🎲{die_roll})"]

    # 2. Resolve and Apply every Determinant individually
    # calculate_determinants returns list: [{label, source_name, field_name, value, op}, ...]
    modifiers = calculate_determinants(event, role_entities)
    
    for m in modifiers:
        val = m['value']
        op = m['op']
        symbol = '×' if op == '*' else '÷' if op == '/' else op
        breakdown_parts.append(f"{symbol} {format_for_display(val)}")
        
        # Update Total
        total = apply_operation(total, val, op)

    # 3. Final Formatting
    breakdown_str = " ".join(breakdown_parts) + \
        f" = <span class='outcome-total'>{format_for_display(total)}</span>"
    
    display_str = ""
    if event.outcome_type == 'fourway':
        span = abs(base_max - base_min) + 1
        shift = round(span * difficulty)

        major_failure_max = base_min + math.floor(shift * 0.20)
        minor_success_min = round(span * 0.10) + shift
        major_success_min = (
            base_max - math.floor(span * 0.15)) + math.floor(shift * 0.40)

        if die_roll == base_max:
            res = "Major Success (Natural)"
        elif die_roll == base_min:
            res = "Major Failure (Natural)"
        elif total >= major_success_min:
            res = "Major Success"
        elif total >= minor_success_min:
            res = "Minor Success"
        elif total <= major_failure_max:
            res = "Major Failure"
        else:
            res = "Minor Failure"

        display_str = f"<b>{res}</b><br><small>{breakdown_str}</small>"
        message_str = f"{format_for_display(total)} — {res}"
    else:
        display_str = breakdown_str
        message_str = f"{format_for_display(total)}"

    add_message(f"{event.name}: Outcome {message_str}")
    return total, display_str

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
        
        rolls_details = " + ".join([f"d{sides}(🎲{r})" for r in rolls])
        bonus_str = f" {'+' if bonus >= 0 else '-'} {abs(bonus)}" if bonus != 0 else ""
        
        display_str = f"{rolls_details}{bonus_str} = <b>{total}</b>"
        numeric_val = float(total)

    elif event.roller_type == RollerType.IRONSWORN:
        action_die = random.randint(1, 6)
        challenge_dice = [random.randint(1, 10), random.randint(1, 10)]
        total = action_die + bonus
        
        hits = sum(1 for die in challenge_dice if total > die)
        res_text = "Strong Hit" if hits == 2 else "Weak Hit" if hits == 1 else "Miss"
        
        display_str = (
            f"{res_text} <br>"
            f"<small>Action: d6(🎲{action_die}) + {bonus} = {total} vs "
            f"[🎲{challenge_dice[0]}][🎲{challenge_dice[1]}]</small>"
        )
        numeric_val = float(hits)

    add_message(f"{event.name}: {display_str.replace('<br>', ' ')}")
    return numeric_val, display_str

# ------------------------------------------------------------------------
# Applying Changes
# ------------------------------------------------------------------------

def apply_event_effects(event, role_entities, roll_outcome):
    """
    Iterates through factors where usage_type == OUT.
    """
    for effect in event.effects:
        if not effect.outfield: continue
        
        # 1. Determine base value to save
        if effect.val_src == 'outcome':
            new_val = roll_outcome
        elif effect.val_src == 'field':
            anchor_id = resolve_anchor_id(effect.outfield.role, role_entities)
            new_val = get_entity_value(anchor_id, effect.outfield)
        else:
            new_val = effect.val_transform
            
        # 2. Transform result (e.g. outcome * 0.5 for half damage)
        if effect.op_transform:
            new_val = apply_operation(new_val, effect.val_transform, effect.op_transform)
            
        # 3. Apply to target
        target_anchor_id = resolve_anchor_id(effect.outfield.role, role_entities)
        
        # Logic from apply_event_change...
        if effect.outfield.field_mode == Participant.ATTR:
            apply_event_change(effect.outfield.attrib_id, 'attrib', target_anchor_id, new_val)
        else:
            apply_event_change(effect.outfield.item_id, 'item', target_anchor_id, new_val)

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
