import random
import math
import logging
from flask import g, request, session
from app.models import (
    db, GENERAL_ID, Entity, Item, Location, Character, Attrib, Event,
    StorageType, Operation, OutcomeType, RollerType, Participant,
    AttribVal, Pile, LocDest, Recipe)
from app.utils import maskable_name
from app.serialization import clone_entity
from .logic_piles import set_quantity, adjust_quantity
from .logic_user_interaction import add_message
from .logic_navigation import get_all_valid_coords, distance_between

logger = logging.getLogger(__name__)

def format_for_display(val):
    if isinstance(val, (list, tuple)):
        return str(tuple(val))
    try:
        return f"{round(val, 2):g}"
    except (TypeError, ValueError):
        return str(val)

# ------------------------------------------------------------------------
# Determinant Logic (Modifiers)
# ------------------------------------------------------------------------

def get_entity_value(anchor_id, field_def, subject_id=None):
    """Handles Attr vs Qty and Base vs Child."""
    game_token=g.game_token
    target_id = anchor_id
    
    # Distance Calculation (Read-Only)
    if field_def.field_mode == Participant.DIST:
        if not subject_id or not anchor_id: return 0.0
        subj = Entity.query.get((game_token, subject_id))
        target = Entity.query.get((game_token, anchor_id))
        if not (subj and target and subj.position and target.position):
            return 0.0
        return float(distance_between(subj.position, target.position) or 0.0)

    # Recipe Property Fetching
    if field_def.field_mode in [Participant.RATE_AMT, Participant.RATE_DUR]:
        if not field_def.recipe_id: return 0.0
        recipe = Recipe.query.get((game_token, field_def.recipe_id))
        if not recipe: return 0.0
        return recipe.rate_amount \
            if field_def.field_mode == Participant.RATE_AMT \
            else recipe.rate_duration

    # Handle Depth Traversal
    if field_def.child_of_anchor:
        # Find the first pile that satisfies the requirement
        if field_def.field_mode == Participant.ATTR:
            pile = db.session.query(Pile).join(
                Item, 
                (Pile.item_id == Item.id) & (Pile.game_token == Item.game_token)
            ).join(
                AttribVal, 
                (AttribVal.subject_id == Item.id) & (AttribVal.game_token == Item.game_token)
            ).filter(
                Pile.game_token == game_token,
                Pile.owner_id == anchor_id,
                AttribVal.attrib_id == field_def.attrib_id
            ).first()
            if not pile: return 0.0
            target_id = pile.item_id 
        else:
            # Quantity Mode: Target is the specific item_id requested
            target_id = field_def.item_id

    # Fetch Attribute Value
    if field_def.field_mode == Participant.ATTR:
        # If entity_id is set, we use that specific Attribute Blueprint
        val_obj = AttribVal.query.filter_by(
            game_token=game_token,
            subject_id=target_id,
            attrib_id=field_def.attrib_id
        ).first()
        return val_obj.value if val_obj else 0.0
    
    # Fetch Quantity Value
    if field_def.field_mode == Participant.QTY and field_def.item_id:
        item = Item.query.get((game_token, field_def.item_id))
        if field_def.role == Participant.UNIVERSAL or (
                item and item.storage_type == StorageType.UNIVERSAL):
            owner_id = GENERAL_ID
        else:
            anchor_ent = Entity.query.get((game_token, anchor_id))
            if anchor_ent and anchor_ent.entity_type == Item.TYPENAME:
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
    if role_name == Participant.UNIVERSAL:
        return GENERAL_ID
    if role_name == Participant.BLUEPRINT:
        return None
    return role_entities.get(role_name)

def can_use_field(field, entity):
    """
    Validation logic to see if the given entity (Char/Loc/Item) 
    can be accessed using the given eventfield.
    """
    game_token = g.game_token

    # --- 1. CHILD TRAVERSAL (Item inside a Location/Character) ---
    if field.child_of_anchor:
        # Check all item piles currently at this location or owned by this character
        # 'entity' here is the Anchor (e.g., The Energy Core Room)
        for pile in entity.piles:
            if field.field_mode == Participant.ATTR:
                # Check if the Item Blueprint has the required attribute
                if any(av.attrib_id == field.attrib_id for av in pile.item.attrib_values):
                    return True
            elif field.field_mode == Participant.QTY:
                if pile.item_id == field.item_id:
                    return True
        return False

    # --- 2. UNIVERSAL STORAGE CHECK ---
    # If the item is universal, only ID 1 (General Storage) can meet it
    if field.field_mode == Participant.QTY and field.item_id:
        item_def = Item.query.get((game_token, field.item_id))
        if item_def and item_def.storage_type == StorageType.UNIVERSAL:
            return entity.id == GENERAL_ID

    # --- 3. STANDARD ATTRIBUTE CHECK (On the Anchor itself) ---
    if field.field_mode == Participant.ATTR and field.attrib_id:
        if not any(av.attrib_id == field.attrib_id for av in entity.attrib_values):
            return False

    # TODO: check for event distance requirement e.g. 30ft (6 tiles)
    # dist = distance_between(owner.position, c.position)
    # if dist is not None and dist > d.distance_reqired

    # --- 4. STANDARD QUANTITY CHECK (On the Anchor itself) ---
    if field.field_mode == Participant.QTY and field.item_id:
        # Check if the entity has a pile of this item
        has_pile = any(p.item_id == field.item_id for p in entity.piles)
        if not has_pile:
            return False

    return True

# app/src/logic_event.py

def is_factor_met(factor, entity, subject_id=None):
    """
    Evaluates if a specific entity satisfies the requirements of an EventFactor.
    Used for UI validation and filtering.
    """
    game_token = g.game_token
    field = factor.infield
    
    # 1. Check if the entity even has the field (The "Capability" check)
    if not field or not can_use_field(field, entity):
        return False if not factor.negate else True

    # 2. If it's a calculation existence is enough to be met
    if not factor.is_comparison:
        return True if not factor.negate else False

    # 3. If it's a comparison (==, >=, etc.), we must check the actual value
    # Fetch the value from the entity
    val = get_entity_value(entity.id, field, subject_id=subject_id)
    
    # Apply inner transform (e.g. Rounding or Softcap)
    if factor.op_transform and factor.op_transform != Operation.CONST:
        val = apply_operation(val, factor.val_transform, factor.op_transform)
    elif factor.op_transform == Operation.CONST:
        val = factor.val_transform

    # Evaluate the comparison: (FetchedVal Op RequiredVal)
    is_satisfied = apply_operation(val, factor.val_required, factor.op_application)
    return is_satisfied if not factor.negate else not is_satisfied

def calculate_determinants(event, role_entities):
    """
    Returns a list of calculated modifiers based on selected participants.
        [{label, source_name, field_name, value, op}, ...]
    """
    modifiers = []
    game_token = g.game_token

    field_name = "Value"
    for det in event.determinants:
        val = 0.0
        breakdown_text = ""
        source_display = "Constant"
            
        if det.op_transform == Operation.CONST:
            val = det.val_transform
        elif det.get_val_from == Participant.INFIELD and det.infield:
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
                field_name = maskable_name(item) if item else "Quantity"

            anchor = Entity.query.get((game_token, anchor_id))
            anchor_name = '' if anchor_id == GENERAL_ID \
                else maskable_name(anchor) if anchor else "Unknown"
            source_display = anchor_name
            if infield.child_of_anchor:
                # Try to find the specific child item that provided the value
                child_name = "Item"
                pile_query = db.session.query(Pile).join(
                    Item, (Pile.item_id == Item.id) & (Pile.game_token == Item.game_token)
                )
                if det.infield.field_mode == Participant.ATTR:
                    pile = pile_query.join(
                        AttribVal, (
                            AttribVal.subject_id == Item.id) & (
                            AttribVal.game_token == Item.game_token)
                    ).filter(
                        Pile.game_token == g.game_token,
                        Pile.owner_id == anchor_id,
                        AttribVal.attrib_id == det.infield.attrib_id
                    ).first()
                else:
                    pile = pile_query.filter(
                        Pile.game_token == g.game_token,
                        Pile.owner_id == anchor_id,
                        Pile.item_id == det.infield.item_id
                    ).first()
                if pile:
                    child_name = maskable_name(pile.item)
                source_display = f"{anchor_name}'s {child_name}"

            breakdown_text = format_for_display(val)

            # Inner Transform: (Val <op> Constant)
            if det.op_transform:
                breakdown_text = get_inner_breakdown(
                    val, det.val_transform, det.op_transform)
                val = apply_operation(
                    val, det.val_transform, det.op_transform)

        # Check if this is a comparison or a calculation
        is_met = True
        if det.is_comparison:
            # Evaluate: (TransformedVal Op ValRequired)
            raw_result = apply_operation(
                val, det.val_required, det.op_application)
            is_met = bool(raw_result)
        if det.negate:
            is_met = not is_met

        # Assemble the enriched dictionary
        modifiers.append({
            'label': det.label or "",
            'source_name': source_display,
            'field_name': field_name,
            'value': val,
            'value_display': format_for_display(val),
            'val_required': det.val_required,
            'negate': det.negate,
            'op_app': det.op_application,
            'op_app_display': det.op_app_display,
            'is_comparison': det.is_comparison,
            'is_met': is_met,
            'breakdown': breakdown_text,
        })
        
    return modifiers

def calculate_effects_targets(event, role_entities):
    """
    Returns a list of current and calculated values for the event's effects.
    """
    results = []
    game_token = g.game_token
    subject_id = resolve_anchor_id(Participant.SUBJECT, role_entities)

    for eff in event.effects:
        field_def = eff.outfield
        if not field_def: continue

        # --- 1. RESOLVE TARGET NAME & CURRENT VALUE (Existing Logic) ---
        target_id = resolve_anchor_id(field_def.role, role_entities)
        current_val = 0.0
        target_name = ""
        is_resolved = False

        if field_def.field_mode == Participant.PLACE:
            is_resolved = True
            loc_id = resolve_anchor_id(Participant.AT, role_entities)
            loc = Entity.query.get((game_token, loc_id))
            item = Item.query.get((game_token, field_def.item_id))
            target_name = f"{maskable_name(loc) if loc else '? loc'}'s" \
                          f" {maskable_name(item) if item else '? item'}"
            current_val = ""
        elif field_def.field_mode in [Participant.RATE_AMT, Participant.RATE_DUR]:
            is_resolved = True 
            target_name = "📦"
            current_val = get_entity_value(None, field_def)
        elif target_id is not None:
            is_resolved = True
            current_val = get_entity_value(target_id, field_def, subject_id)

            if field_def.child_of_anchor and \
                    field_def.field_mode == Participant.ATTR:
                pile = db.session.query(Pile).join(
                    Item, 
                    (Pile.item_id == Item.id) &
                    (Pile.game_token == Item.game_token)
                ).join(
                    AttribVal, 
                    (AttribVal.subject_id == Item.id) &
                    (AttribVal.game_token == Item.game_token)
                ).filter(
                    Pile.game_token == game_token,
                    Pile.owner_id == target_id,
                    AttribVal.attrib_id == field_def.attrib_id
                ).first()
                if pile:
                    parent_name = "🌐" if target_id == GENERAL_ID \
                        else maskable_name(
                            Entity.query.get((game_token, target_id)))
                    target_name = f"{parent_name}'s {maskable_name(pile.item)}"
                else:
                    target_name = "🌐" if target_id == GENERAL_ID \
                        else maskable_name(
                            Entity.query.get((game_token, target_id)))
            else:
                target_name = "🌐" if target_id == GENERAL_ID \
                    else maskable_name(
                        Entity.query.get((game_token, target_id)))
        else:
            target_name = "(" + field_def.role + ")"
            is_resolved = False

        # --- 2. RESOLVE SOURCE DATA (Existing Logic) ---
        source_name = ""
        source_val = 0.0
        
        if eff.get_val_from == Participant.OUTCOME:
            source_name = "Roll Result"
        elif eff.get_val_from in (Participant.INFIELD, Participant.OUTFIELD):
            field = eff.infield or eff.outfield
            anchor_id = resolve_anchor_id(field.role, role_entities)
            source_name = field.get_field_name()
            if field.field_mode in [Participant.RATE_AMT, Participant.RATE_DUR] or anchor_id:
                source_val = get_entity_value(anchor_id, field, subject_id)
        else:
            source_val = eff.val_transform

        # --- 3. CALCULATE PRE-ROLL IMPACT ---
        impact_value = None
        if eff.get_val_from == Participant.OUTCOME and \
                eff.op_transform != Operation.CONST:
            relies_on_roll = True
        else:
            relies_on_roll = False
            impact_value = source_val
            if eff.op_transform:
                impact_value = apply_operation(
                    impact_value, eff.val_transform, eff.op_transform)

        results.append({
            'effect_id': eff.id,
            'target_name': target_name,
            'current_value': current_val,
            'current_display': format_for_display(current_val),
            'is_resolved': is_resolved,
            'source_name':  "Constant" if eff.op_transform == Operation.CONST \
                            else source_name,
            'source_value': source_val,
            'source_display': format_for_display(source_val),
            'impact_value': impact_value, # The result of the inner math
            'impact_display': format_for_display(impact_value) \
                              if impact_value is not None else None,
            'relies_on_roll': relies_on_roll,
            'op_app': eff.op_application,
            'op_app_display': eff.op_app_display,
            'op_trans': eff.op_transform,
            'val_trans': eff.val_transform,
            'get_val_from': eff.get_val_from
        })
    return results

def calculate_solved_effects(event, role_entities, roll_total):
    """
    Calculates the final outcome of every effect for previewing 
    after a roll has occurred, but before it is applied.
    """
    results = []
    game_token = g.game_token
    subject_id = resolve_anchor_id(Participant.SUBJECT, role_entities)

    for eff in event.effects:
        # 1. Resolve Target Value
        target_id = resolve_anchor_id(eff.outfield.role, role_entities)
        current_val = 0.0
        if eff.outfield.field_mode in [Participant.RATE_AMT, Participant.RATE_DUR]:
             current_val = get_entity_value(None, eff.outfield)
        elif target_id is not None:
            current_val = get_entity_value(target_id, eff.outfield, subject_id)

        # 2. Calculate the "Impact" (The 'From' side)
        if eff.get_val_from == Participant.OUTCOME:
            impact = roll_total
        elif eff.get_val_from in (Participant.INFIELD, Participant.OUTFIELD):
            source_field = eff.infield or eff.outfield
            anchor_id = resolve_anchor_id(source_field.role, role_entities)
            impact = get_entity_value(anchor_id, source_field, subject_id)
        else:
            impact = eff.val_transform

        # Apply inner transform (e.g. Round, Softcap)
        if eff.op_transform:
            impact = apply_operation(impact, eff.val_transform, eff.op_transform)

        # 3. Calculate Final Result (The 'To' side)
        if eff.op_application == Operation.ASSIGN:
            final_val = impact
        else:
            final_val = apply_operation(current_val, impact, eff.op_application)
        if eff.outfield and eff.outfield.field_mode == Participant.PLACE:
            final_display = "Placed"
        else:
            final_display = format_for_display(final_val)

        results.append({
            'effect_id': eff.id,
            'impact_value': impact,
            'impact_display': format_for_display(impact),
            'final_value': final_val,
            'final_display': final_display
        })
    return results

def apply_operation(current_val, mod_val, op):
    """Applies the specific operation and returns the new value."""
    if op == Operation.CONST:
        return mod_val

    # Unary Functions
    if op == Operation.SOFTCAP:
        c = 50
        if current_val == 0:
            return 0.0
        current_abs = abs(current_val)
        sign = 1 if current_val > 0 else -1
        if current_abs < 1:
            return current_val
        if current_abs < 1.1:
            return sign * 1
        ratio = math.log(1 + current_abs / c) / (
            1 + math.log(1 + current_abs / c))
        return sign * (1 + mod_val * ratio)
    if op == Operation.ROUND:
        try:
            if mod_val == 0:
                return current_val
            # Round to nearest X
            return float(round(current_val / mod_val) * mod_val)
        except (ValueError, TypeError, ZeroDivisionError):
            return float(round(current_val))

    # Comparisons
    if op == Operation.EQ: return current_val == mod_val
    if op == Operation.GE: return current_val >= mod_val
    if op == Operation.LT: return current_val < mod_val
    if op == Operation.NE: return current_val != mod_val

    # Arithmetic
    if op == Operation.ADD:  return current_val + mod_val
    if op == Operation.SUB:  return current_val - mod_val
    if op == Operation.MULT: return current_val * mod_val
    if op == Operation.DIV:  return current_val / mod_val \
                                if mod_val != 0 else current_val
    if op == Operation.VAL_TO_POW: return current_val ** mod_val
    if op == Operation.POW_OF_VAL: return mod_val ** current_val

    return current_val

def get_inner_breakdown(val, mod_val, op):
    """Formats the inner transformation for the UI."""
    v = format_for_display(val)
    m = format_for_display(mod_val)
    formats = {
        Operation.CONST:      m,
        Operation.ADD:        f"{v}+{m}",
        Operation.SUB:        f"{v}-{m}",
        Operation.MULT:       f"{v}×{m}",
        Operation.DIV:        f"{v}÷{m}",
        Operation.VAL_TO_POW: f"{v}<sup>{m}</sup>",
        Operation.POW_OF_VAL: f"{m}<sup>{v}</sup>",
        Operation.SOFTCAP:    f"{v} Soft Capped",
        Operation.ROUND  :    f"Round {v}, {m}"
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
    total = 0
    if event.outcome_type == 'determined':
        total = event.single_number
        breakdown_parts = [format_for_display(total)]

    elif event.outcome_type == 'selection':
        options = [s.strip() for s in event.selection_strings.split('\n') if s.strip()]
        choice = random.choice(options) if options else "Nothing"
        breakdown_parts = [f"Selection: <b>{choice}</b>"]

    elif event.outcome_type == 'coordinates':
        loc_id = role_entities.get(Participant.AT)
        total, coord_str = roll_coordinate(loc_id)
        breakdown_parts = [coord_str]

    else:
        die_roll = random.randint(base_min, base_max)
        total = float(die_roll)
        breakdown_parts = [f"d{base_max - base_min + 1}(🎲{die_roll})"]

    # 2. Resolve and Apply every Determinant individually
    modifiers = calculate_determinants(event, role_entities)
    
    PRECEDENCE = {
        Operation.ADD:        1, Operation.SUB:        1,
        Operation.MULT:       2, Operation.DIV:        2,
        Operation.VAL_TO_POW: 3, Operation.POW_OF_VAL: 3,
    }
    breakdown_str = breakdown_parts[0] # Start with the Die Roll/Base
    current_min_precedence = 99 

    for m in modifiers:
        val = m['value']
        op = m['op']
        if m['is_comparison']:
            continue

        symbol = Operation.Repr[op]
        formatted_val = format_for_display(val)

        # If the new operator is higher precedence than the previous ones,
        # wrap the left side to maintain sequential logic.
        # Example: (1 + 1) × 4
        op_prec = PRECEDENCE.get(op, 1)
        if op_prec > current_min_precedence:
            breakdown_str = f"({breakdown_str})"
        if op == Operation.VAL_TO_POW:
            breakdown_str = f"{breakdown_str}<sup>{formatted_val}</sup>"
        elif op == Operation.POW_OF_VAL:
            breakdown_str = f"{formatted_val}<sup>{breakdown_str}</sup>"
        else:
            breakdown_str = f"{breakdown_str} {symbol} {formatted_val}"
        current_min_precedence = op_prec

        # Update Total
        total = apply_operation(total, val, op)

    # 3. Final Formatting
    if event.outcome_type not in('selection', 'coordinates'):
        breakdown_str += \
            f" = <span class='outcome-total'>{format_for_display(total)}</span>"
    
    display_str = ""
    tier = None
    if event.outcome_type == 'fourway':
        span = abs(base_max - base_min) + 1
        shift = round(span * difficulty)

        major_failure_max = base_min + math.floor(shift * 0.20)
        minor_success_min = round(span * 0.10) + shift
        major_success_min = (
            base_max - math.floor(span * 0.15)) + math.floor(shift * 0.40)

        if die_roll == base_max:
            res = "Natural Max!"
            tier = Participant.SUCCESS_NAT_MAX
        elif die_roll == base_min:
            res = "Natural Min!"
            tier = Participant.FAILURE_NAT_MIN
        elif total >= major_success_min:
            res = "Major Success"
            tier = Participant.SUCCESS_MAJOR
        elif total >= minor_success_min:
            res = "Minor Success"
            tier = Participant.SUCCESS_MINOR
        elif total <= major_failure_max:
            res = "Major Failure"
            tier = Participant.FAILURE_MAJOR
        else:
            res = "Minor Failure"
            tier = Participant.FAILURE_MINOR

        display_str = f"<b>{res}</b><br><small>{breakdown_str}</small>"
        message_str = f"{format_for_display(total)} — {res}"
    else:
        display_str = breakdown_str
        message_str = format_for_display(total)

    add_message(f"{event.name}: Outcome {message_str}")
    return total, display_str, tier

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
    chosen_pos = random.choice(available)
    return chosen_pos, f"Coordinates: {chosen_pos}"

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

def effect_description(eff):
    """Returns a string describing the math logic of an effect."""
    # The Source
    if eff.get_val_from == Participant.OUTCOME:
        source_val = "[Roll Result]"
    else:
        field = eff.infield or eff.outfield
        source_val = field.get_field_name() if field else "Value"

    # The Inner Transform
    if eff.op_transform == Operation.CONST:
        source_val = format_for_display(eff.val_transform)
    elif eff.op_transform:
        # e.g., (Roll Result * 2)
        REPLACE = 135.79
        source_val = get_inner_breakdown(
            REPLACE, eff.val_transform, eff.op_transform).replace(
            str(REPLACE), source_val)

    # The Outer Application
    if eff.op_application == Operation.ASSIGN:
        op_app_repr = "→"
    else:
        op_app_repr = Operation.Repr[eff.op_application]
    
    return f"{op_app_repr} {source_val}"

def process_all_effects(event, role_entities, roll_total, tier, force_auto_only=False):
    """
    Called by roll_event route. Scans all effects and triggers
    automatic ones that match the success tier.
    """
    for eff in event.effects:
        if not check_outcome_success(eff.outcome_success, tier):
            continue
        if force_auto_only and not eff.auto_apply:
            continue
        do_effect_change(eff, roll_total, role_entities)

def check_outcome_success(filter_val, tier):
    if filter_val == Participant.ALWAYS:
        return True
    if tier is None:
        return False
    if filter_val == Participant.SUCCESS_ANY:
        return tier in [
            Participant.SUCCESS_NAT_MAX, 
            Participant.SUCCESS_MAJOR, 
            Participant.SUCCESS_MINOR
        ]
    if filter_val == Participant.FAILURE_ANY:
        return tier in [
            Participant.FAILURE_NAT_MIN, 
            Participant.FAILURE_MAJOR, 
            Participant.FAILURE_MINOR
        ]
    if filter_val == Participant.SUCCESS_MAJOR:
        return tier in [Participant.SUCCESS_NAT_MAX, Participant.SUCCESS_MAJOR]
    if filter_val == Participant.FAILURE_MAJOR:
        return tier in [Participant.FAILURE_NAT_MIN, Participant.FAILURE_MAJOR]

    # Exact matches: Natural Max, Natural Min, Minor Success, Minor Failure
    return filter_val == tier

def do_effect_change(eff, roll_total, role_entities):
    """
    Calculates math, writes to DB, and logs the change.
    """
    game_token = g.game_token
    subject_id = resolve_anchor_id(Participant.SUBJECT, role_entities)

    # --- STEP 1: CALCULATE IMPACT (The "From") ---
    if eff.get_val_from == Participant.OUTCOME:
        impact = roll_total
    elif eff.get_val_from in (Participant.INFIELD, Participant.OUTFIELD):
        source_field = eff.infield or eff.outfield
        anchor_id = resolve_anchor_id(source_field.role, role_entities)
        impact = get_entity_value(anchor_id, source_field, subject_id)
    else:
        impact = eff.val_transform

    if eff.op_transform:
        impact = apply_operation(impact, eff.val_transform, eff.op_transform)

    # --- STEP 2: APPLY TO DATABASE (The "To") ---
    field_def = eff.outfield
    if not field_def:
        return True, ''

    # If the mode requires a specific instance (Character/Location/Pile), 
    # validate that the participant role is resolved.
    if field_def.field_mode not in Participant.USES_BLUEPRINT:
        out_entity_id = resolve_anchor_id(field_def.role, role_entities)
        if out_entity_id is None:
            return False, f"No entity available for role {field_def.role}"
    op = eff.op_application

    # Destination A: Attributes
    if field_def.field_mode == Participant.ATTR:
        if field_def.child_of_anchor:
            pile = db.session.query(Pile).join(
                Item, 
                (Pile.item_id == Item.id) & (Pile.game_token == Item.game_token)
            ).join(
                AttribVal, 
                (AttribVal.subject_id == Item.id) & (AttribVal.game_token == Item.game_token)
            ).filter(
                Pile.game_token == game_token,
                Pile.owner_id == out_entity_id,
                AttribVal.attrib_id == field_def.attrib_id
            ).first()
            if pile:
                out_entity_id = pile.item_id
            else:
                anchor = Entity.query.get((game_token, out_entity_id))
                anchor_name = maskable_name(anchor) if anchor else "(Unknown)"
                attr = Attrib.query.get((game_token, field_def.attrib_id))
                attr_name = attr.name if attr else "(Unknown)"
                return False, f"Could not find an item at {anchor_name}" \
                              f" that has {attr_name}."

        record = AttribVal.query.filter_by(
            game_token=game_token,
            subject_id=out_entity_id,
            attrib_id=field_def.attrib_id
        ).first()
        current = record.value if record else 0.0
        new_val = impact if op == Operation.ASSIGN \
            else apply_operation(current, impact, op)
        
        if not record:
            db.session.add(AttribVal(
                game_token=game_token, subject_id=out_entity_id, 
                attrib_id=field_def.attrib_id, value=new_val))
        else:
            record.value = new_val

    # Destination B: Item Quantities
    elif field_def.field_mode == Participant.QTY:
        from .logic_piles import get_accessible_quantity, set_quantity
        # Items are a special case because we want to use the existing pile logic
        # that handles deleting empty piles.
        current = get_accessible_quantity(field_def.item_id, out_entity_id)
        new_val = impact if op == Operation.ASSIGN \
            else apply_operation(current, impact, op)
        set_quantity(field_def.item_id, out_entity_id, new_val)

    # Destination C: Recipe Efficiency (Global Blueprint Change)
    elif field_def.field_mode in [Participant.RATE_AMT, Participant.RATE_DUR]:
        recipe = Recipe.query.get((game_token, field_def.recipe_id))
        if recipe:
            if field_def.field_mode == Participant.RATE_AMT:
                current = recipe.rate_amount
                recipe.rate_amount = impact if op == Operation.ASSIGN \
                    else apply_operation(current, impact, op)
                log_impact = f"yield to {recipe.rate_amount:g}"
            else:
                current = recipe.rate_duration
                new_dur = impact if op == Operation.ASSIGN else \
                    apply_operation(current, impact, op)
                # Truncate to integer and clamp at 1 second minimum
                recipe.rate_duration = max(1, int(impact))
                log_impact = "duration to {recipe.rate_duration:g}"

            add_message(
                f"Set {maskable_name(recipe.product)} {log_impact}")

    # Destination D: Physical Placement
    elif field_def.field_mode == Participant.PLACE:
        
        # 1. Target must be a location (The 'At' participant)
        loc_id = resolve_anchor_id(Participant.AT, role_entities)
        if not loc_id:
            return False, "No location (At) for placement."

        # 2. Extract position from the roll result
        if not isinstance(roll_total, list) or len(roll_total) != 2:
            return False, "Expected a Coordinate outcome."

        # 3. Create or increment the pile
        new_qty = adjust_quantity(
            field_def.item_id, 
            loc_id, 
            delta=1.0, 
            position=roll_total
        )
        
        item = Item.query.get((game_token, field_def.item_id))
        add_message(f"Placed {maskable_name(item)} at {roll_total}")

    # Destination E: Teleportation (Move existing character)
    elif field_def.field_mode == Participant.POS:
        if not isinstance(roll_total, list) or len(roll_total) != 2:
            return False, "Expected a Coordinate outcome."
            
        char = Character.query.get((game_token, out_entity_id))
        if not char:
            return False, "Expected a character."

        loc_id = resolve_anchor_id(Participant.AT, role_entities)
        loc = Location.query.get((game_token, loc_id))
        char.location_id = loc_id
        char.position = roll_total
        add_message(
            f"Positioned {char.name} at {maskable_name(loc)} {roll_total}")

    # Destination F: Mob Spawning (Clone a character)
    elif field_def.field_mode == Participant.SPAWN:
        if not isinstance(roll_total, (list, tuple)) or len(roll_total) != 2:
            return False, "Expected a Coordinate outcome."

        char = clone_entity(field_def.char_id, 'character')
        if not char:
            return False, "Character not found."

        # Position the clone at the rolled coordinates
        loc_id = resolve_anchor_id(Participant.AT, role_entities)
        loc = Location.query.get((game_token, loc_id))
        char.location_id = loc_id
        char.position = roll_total
        add_message(f"Spawned {char.name} at {maskable_name(loc)} {roll_total}")

    db.session.commit()
    return True, ''
