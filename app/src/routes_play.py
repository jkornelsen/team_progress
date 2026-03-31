import logging
from flask import (
    Blueprint, render_template, request, jsonify, g, session, current_app)
from app.models import (
    db, Entity, Item, Character, Location, Event, Pile,
    Recipe, RecipeAttribReq, LocationDest, AttribValue,
    Progress, Overall, WinRequirement, GameMessage, GENERAL_ID)
from app.utils import LinkLetters, capture_origin, redirect_back
from app.src.logic_piles import (
    transfer_item, get_character_piles, ensure_owner_up_to_date)
from app.src.logic_progress import (
    start_production, stop_production, update_progress, can_perform_recipe)
from app.src.logic_event import TriggerException
from app.src.logic_navigation import (
    move_group, get_available_destinations, arrive_at_destination)
from app.src.logic_objectives import validate_requirements

logger = logging.getLogger(__name__)
play_bp = Blueprint('play', __name__)

# ------------------------------------------------------------------------
# The Overview (Dashboard)
# ------------------------------------------------------------------------

@play_bp.route('/overview')
def overview():
    game_token = g.game_token
    
    # 1. Fetch Top-Level Entities
    chars = Character.query.filter_by(game_token=game_token, toplevel=True).all()
    locs = Location.query.filter_by(game_token=game_token, toplevel=True).all()
    items = Item.query.filter_by(game_token=game_token, toplevel=True).all()
    events = Event.query.filter_by(game_token=game_token, toplevel=True).all()
    
    # 2. Check Win Requirements
    win_reqs, all_met = validate_requirements(game_token)
    overall = Overall.query.get(game_token)
    
    # 3. Recent Messages
    messages = GameMessage.query.filter_by(game_token=game_token)\
                .order_by(GameMessage.timestamp.desc()).limit(20).all()

    return render_template(
        'play/overview.html',
        characters=chars,
        locations=locs,
        items=items,
        events=events,
        overall=overall,
        win_reqs=win_reqs,
        all_requirements_met=all_met,
        link_letters=LinkLetters(excluded='m'),
        messages=messages
    )

# ------------------------------------------------------------------------
# Item & Pile Routes
# ------------------------------------------------------------------------

@play_bp.route('/play/item/<int:id>')
def play_item(id):
    game_token = g.game_token
    item = Item.query.get_or_404((game_token, id))
    capture_origin(name=item.name)
    
    # 1. Determine Context (Who owns the pile we are looking at?)
    char_id = request.args.get('char_id', type=int)
    loc_id = request.args.get('loc_id', type=int)
    
    if char_id:
        owner = Character.query.get((game_token, char_id))
    elif loc_id:
        owner = Location.query.get((game_token, loc_id))
    else:
        # Default to General Storage
        owner = Entity.query.get((game_token, GENERAL_ID))
    if not owner:
        from flask import abort
        abort(404, description="Item owner not found.")

    # 2. Fetch the specific Pile record
    query = Pile.query.filter_by(
        game_token=game_token, 
        item_id=id, 
        owner_id=owner.id
    )
    raw_pos = request.args.getlist('pos[]')
    if raw_pos:
        pos = tuple(int(x) for x in raw_pos)
        query = query.filter_by(position=pos)
    else:
        pos = None
        query = query.filter_by(position=None)
    pile = query.first()

    # If no pile exists yet, create a virtual one for the UI
    if not pile:
        pile = Pile(item_id=id, owner_id=owner.id, quantity=0.0, position=pos)

    # 3. Enrich Recipes with Failure Reasons
    # This allows the UI to show the 🚫 icon and the specific reason tooltip immediately
    enriched_recipes = []
    for r in item.recipes:
        can_do, reason = can_perform_recipe(game_token, owner.id, r)
        
        # We also want the UI to know current stock of ingredients
        sources_with_stock = []
        for s in r.sources:
            stock_pile = Pile.query.filter_by(
                game_token=game_token, item_id=s.item_id, owner_id=owner.id
            ).first()
            sources_with_stock.append({
                'ingredient': s.ingredient, # JTI relationship
                'q_required': s.q_required,
                'preserve': s.preserve,
                'current_stock': stock_pile.quantity if stock_pile else 0.0
            })

        enriched_recipes.append({
            'id': r.id,
            'rate_amount': r.rate_amount,
            'rate_duration': r.rate_duration,
            'instant': r.instant,
            'can_produce': can_do,
            'reason': reason,
            'sources': sources_with_stock
        })

    # 4. Check for active progress
    current_progress = Progress.query.filter_by(
        game_token=game_token, host_id=owner.id
    ).first()

    # 5. UI Context: Characters nearby (for the "Pick Up" button)
    chars_here = []
    if loc_id:
        chars_here = Character.query.filter_by(game_token=game_token, location_id=loc_id).all()

    return render_template(
        'play/item.html',
        item=item,
        owner=owner,
        pile=pile,
        recipes=enriched_recipes,
        progress=current_progress,
        chars_here=chars_here
    )

@play_bp.route('/char/<int:id>/drop', methods=['POST'])
def drop_item(id):
    item_id = req.get_int('item_id')
    qty = req.get_float('quantity')
    
    # Get character to find current location
    char = Character.query.get((g.game_token, id))
    
    # Transfer from Char to Location at current Char position
    success = transfer_item(
        item_id, from_owner_id=id, to_owner_id=char.location_id,
        quantity=qty, to_pos=char.position)
    
    if success:
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@play_bp.route('/char/<int:id>/pickup', methods=['POST'])
def pickup_item():
    item_id = req.get_int('item_id')
    qty = req.get_float('quantity')
    pos = [int(x) for x in req.get_list('pos')] # e.g. [1, 2]
    
    char = Character.query.get((g.game_token, id))
    
    # Transfer from Location to Char
    success = transfer_item(
        item_id, from_owner_id=char.location_id, to_owner_id=id,
        quantity=qty, from_pos=pos
    )
    
    if success:
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

# ------------------------------------------------------------------------
# Character & Location Routes
# ------------------------------------------------------------------------

@play_bp.route('/play/char/<int:id>')
def play_character(id):
    game_token = g.game_token
    character = Character.query.get_or_404((game_token, id))
    capture_origin(name=character.name)
    
    # 1. Fetch piles (Items carried)
    inventory = Pile.query.filter_by(
        game_token=game_token, owner_id=id
    ).all()
    
    # 2. Fetch Attributes (Stats)
    attrib_values = AttribValue.query.filter_by(
        game_token=game_token, subject_id=id
    ).all()
    
    # 3. Fetch Navigation (Nearby Destinations)
    destinations, has_nonadjacent = get_available_destinations(character)
    
    # 4. Fetch Abilities (Events linked to this character)
    # Assuming a relationship 'abilities' exists in the Character model
    # Or query EventRegistry/Triggers
    abilities = Event.query.filter_by(game_token=game_token, toplevel=True).all() # Placeholder logic

    return render_template(
        'play/character.html',
        character=character,
        inventory=inventory,
        attrib_values=attrib_values,
        destinations=destinations,
        has_nonadjacent=has_nonadjacent,
        abilities=abilities,
        link_letters=LinkLetters(excluded='moe')
    )

@play_bp.route('/play/location/<int:id>')
def play_location(id):
    game_token = g.game_token
    location = Location.query.get_or_404((game_token, id))
    capture_origin(name=location.name)
    
    # Fetch Items on the ground
    inventory_piles = Pile.query.filter_by(
        game_token=game_token, owner_id=id
    ).all()
    
    # Fetch Characters present here
    characters_here = Character.query.filter_by(
        game_token=game_token, location_id=id
    ).all()
    
    # Fetch Exits (Destinations)
    destinations = LocationDest.query.filter(
        LocationDest.game_token == game_token,
        ((LocationDest.loc1_id == id) | 
         ((LocationDest.loc2_id == id) & (LocationDest.bidirectional == True)))
    ).all()

    # Fetch Referenced Items and their "General Storage" (ID 1) quantities
    referenced_data = []
    for item in location.item_refs:
        # Check stock in General Storage
        gen_pile = Pile.query.filter_by(
            game_token=game_token, item_id=item.id, owner_id=GENERAL_ID
        ).first()
        
        referenced_data.append({
            'item': item,
            'quantity': gen_pile.quantity if gen_pile else 0.0
        })

    # Local attributes (e.g., 'Danger Level', 'Temperature')
    attrib_values = AttribValue.query.filter_by(
        game_token=game_token, subject_id=id
    ).all()

    # Which character is currently being controlled for movement
    active_char_id = request.args.get('active_char_id', type=int)
    if not active_char_id and characters_here:
        active_char_id = characters_here[0].id

    return render_template(
        'play/location.html',
        location=location,
        inventory_piles=inventory_piles,
        characters_here=characters_here,
        destinations=destinations,
        referenced_items=referenced_data,
        attrib_values=attrib_values,
        active_char_id=active_char_id,
        link_letters=LinkLetters(excluded='moe')
    )

@play_bp.route('/char/move/<int:id>', methods=['POST'])
def char_move(id):
    req = RequestHelper('form')
    dx = req.get_int('dx')
    dy = req.get_int('dy')
    
    move_with = req.get_list('move_with')
    success, results = move_group(id, dx, dy, move_with)
    if success:
        return jsonify({"status": "success", "positions": results})
    return jsonify({"status": "error", "message": results}), 400

@play_bp.route('/char/go/<int:id>', methods=['POST'])
def char_travel(id):
    req = RequestHelper('form')
    dest_loc_id = req.get_int('dest_id')
    move_with = req.get_list('move_with')
    success, message = arrive_at_destination(id, dest_loc_id, move_with)
    if success:
        return jsonify({"status": "arrived"})
    else:
        return jsonify({"status": "error", "message": message}), 400

# ------------------------------------------------------------------------
# Progress & Production Routes
# ------------------------------------------------------------------------

@play_bp.route('/production/start/host/<int:id>', methods=['POST'])
def start_item_production():
    recipe_id = req.get_int('recipe_id')
    
    success, message = start_production(id, recipe_id)
    return jsonify({"status": "success" if success else "error", "message": message})

@play_bp.route('/production/stop/host/<int:id>', methods=['POST'])
def stop_item_production():
    if stop_production(id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@play_bp.route('/production/status/host/<int:id>')
def production_status(id):
    """Heartbeat endpoint to calculate current progress and refresh recipe availability."""
    game_token = g.game_token
    item_id = request.args.get('item_id', type=int) # Help route find definition
    
    try:
        # 1. Tick the logic
        prog = Progress.query.filter_by(game_token=game_token, owner_id=id).first()
        if prog and prog.is_ongoing:
            update_progress(prog.id)
        
        # 2. Build Response Base
        start_time_iso = None
        if prog and prog.start_time:
            start_time_iso = prog.start_time.isoformat()
        res = {
            "is_ongoing": bool(prog and prog.is_ongoing),
            "batches": prog.batches_processed if prog else 0,
            "start_time": start_time_iso,
            "active_recipe_id": prog.recipe_id if prog else None,
            "recipes": []
        }

        # 3. Dynamic Recipe Validation
        # If we know which item page the user is on, re-verify all recipes for the 🚫 icons
        if item_id:
            recipes = Recipe.query.filter_by(game_token=game_token, product_id=item_id).all()
            for r in recipes:
                can_do, reason = can_perform_recipe(game_token, id, r)
                res["recipes"].append({
                    "recipe_id": r.id,
                    "can_produce": can_do,
                    "reason": reason
                })

        return jsonify(res)

    except TriggerException as e:
        return jsonify({
            "status": "interrupt",
            "message": e.message,
            "event_id": e.event_id
        })

# ------------------------------------------------------------------------
# Events & Dice
# ------------------------------------------------------------------------

@play_bp.route('/play/event/<int:id>')
def play_event(id):
    game_token = g.game_token
    event = Event.query.get_or_404((game_token, id))
    capture_origin(name=event.name)
    
    # 1. Get Context (Who is acting?)
    source_id = request.args.get('source_id', type=int)
    
    # 2. Fetch Eligible Participants (for the dropdowns)
    # We query the base Entity table because ANY entity might have the required attribute
    eligible_sources = Entity.query.filter_by(game_token=game_token).all()
    eligible_targets = eligible_sources # Simplified
    
    # 3. Pre-calculate Determinants (Modifiers)
    # This logic helps the UI show things like "Strength (+5)" before rolling
    determinants = []
    if source_id:
        for det in getattr(event, 'determinants', []):
            from .logic_event import get_entity_value
            val = get_entity_value(game_token, source_id, det.attrib_id, det.item_id)
            determinants.append({
                'label': det.label,
                'operation': det.operation,
                'mode': det.mode,
                'value': val
            })

    return render_template(
        'play/event.html',
        event=event,
        source_id=source_id,
        eligible_sources=eligible_sources,
        eligible_targets=eligible_targets,
        determinants=determinants
    )

@play_bp.route('/event/roll/<int:id>', methods=['POST'])
def roll_event(id):
    die_min = req.get_float('die_min', 1.0)
    die_max = req.get_float('die_max', 20.0)
    loc_id = req.get_int('location_id')

    result_num, result_str = roll_for_outcome(
        id, die_min, die_max, loc_id)
    
    return jsonify({
        "result_value": result_num,
        "display": result_str
    })

@play_bp.route('/event/apply/<int:id>', methods=['POST'])
def apply_event(id):
    """Apply a roll result to a specific container."""
    # 1. The thing we are changing (The Key)
    key_id = req.get_int('key_id')
    key_type = req.get_str('key_type') # 'attrib' or 'item'
    
    # 2. Who owns it (The Container)
    container_id = req.get_int('container_id')
    
    # 3. The new calculated value from the UI
    new_value = req.get_float('new_value')

    if not key_id or not container_id:
        return jsonify({"status": "error", "message": "Missing target info"}), 400

    from .logic_event import apply_event_change
    apply_event_change(key_id, key_type, container_id, new_value)
    
    # Log to Chronicle
    container = Entity.query.get((g.game_token, container_id))
    key_def = Entity.query.get((g.game_token, key_id))
    add_message(g.game_token, f"Updated {key_def.name} on {container.name} to {new_value}")

    return jsonify({"status": "success"})

@play_bp.route('/play/attrib/<int:attrib_id>/<int:subject_id>', methods=['GET', 'POST'])
def play_attrib(attrib_id, subject_id):
    game_token = g.game_token
    attribute = Attrib.query.get_or_404((game_token, attrib_id))
    subject = Entity.query.get_or_404((game_token, subject_id))
    
    val_record = AttribValue.query.filter_by(
        game_token=game_token, attrib_id=attrib_id, subject_id=subject_id
    ).first()
    if not val_record:
        val_record = AttribValue(
            game_token=game_token, attrib_id=attrib_id,
            subject_id=subject_id, value=0.0)
        db.session.add(val_record)

    if request.method == 'POST':
        op = req.get_str('operator')
        
        if op == 'set':
            new_val = req.get_float('value') or req.get_float('operand')
        else:
            operand = req.get_float('operand')
            if op == '+': new_val = val_record.value + operand
            elif op == '-': new_val = val_record.value - operand
            elif op == '*': new_val = val_record.value * operand
            elif op == '/': new_val = val_record.value / operand if operand != 0 else val_record.value
        
        val_record.value = new_val
        db.session.commit()
        
        # Log to Chronicle
        add_message(game_token, f"Modified {attribute.name} on {subject.name} to {new_val}")
        #return redirect(url_for('play.play_attrib', attrib_id=attrib_id, subject_id=subject_id))
        return redirect_back()

    # Get reverse dependencies (items needing this for recipes)
    items_requiring_this = Item.query.join(Recipe).join(RecipeAttribReq).filter(
        RecipeAttribReq.attrib_id == attrib_id,
        Item.game_token == game_token
    ).all()

    return render_template('play/attrib.html', 
                           attribute=attribute, 
                           owner=owner, 
                           attrib_value=val_record,
                           items_requiring_this=items_requiring_this)
