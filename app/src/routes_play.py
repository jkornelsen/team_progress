import logging
from flask import (
    Blueprint, render_template, request, jsonify, g, session, current_app)
from app.models import (
    db, Entity, Item, Character, Location, Attrib, Event,
    Pile, AttribVal, Operation, OutcomeType,
    Recipe, RecipeAttribReq, LocDest,
    Progress, Overall, WinRequirement, GameMessage,
    GENERAL_ID, StorageType, Participant)
from app.utils import (
    RequestHelper, format_num, parse_coords, LinkLetters,
    capture_origin, redirect_back)
from .logic_piles import transfer_item
from .logic_event import (
    roll_for_outcome, roll_for_system_outcome, TriggerException, calculate_determinants,
    get_entity_value)
from .logic_progress import (
    update_progress, tick_all_active, start_production, stop_production)
from .logic_production import (
    find_best_host, resolve_recipe_sources, can_perform_recipe,
    execute_production)
from .logic_navigation import (
    move_group, get_available_destinations, arrive_at_destination,
    is_in_grid, get_default_position, is_adjacent)
from .logic_objectives import validate_requirements
from .logic_user_interaction import add_message

logger = logging.getLogger(__name__)
play_bp = Blueprint('play', __name__)

def get_inventory_for_role(entity):
    """Returns all piles (inventory items) for the given entity."""
    return entity.piles

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
                .order_by(GameMessage.timestamp.desc()).limit(30).all()
    messages.reverse()

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
# Location & Character Routes
# ------------------------------------------------------------------------

@play_bp.route('/play/location/<int:id>')
def play_location(id):
    game_token = g.game_token
    location = Location.query.get_or_404((game_token, id))
    capture_origin(name=location.name)
    session['old_loc_id'] = id
    
    has_grid = bool(location.dimensions and location.dimensions[0] > 0)
    
    # 1. Fetch Characters & Items
    characters_here = Character.query.filter_by(
        game_token=game_token, location_id=id
    ).all()
    
    inventory_piles = Pile.query.filter_by(
        game_token=game_token, owner_id=id
    ).all()

    # Validate the session's char_id
    current_char_id = session.get('old_char_id')
    current_char = next(
        (c for c in characters_here if c.id == current_char_id), None)
    if not current_char:
        session.pop('old_char_id', None)

    # 2. Fix Incorrectly Positioned Entities
    if has_grid:
        default_pos = get_default_position(location)
        needs_commit = False

        if default_pos:
            # Validate Characters
            for char in characters_here:
                if not char.position or not is_in_grid(location, *char.position):
                    char.position = default_pos
                    db.session.add(char)
                    needs_commit = True

            # Validate & Merge Items
            for pile in inventory_piles:
                if not pile.position or not is_in_grid(location, *pile.position):
                    pile.merge_to(default_pos)
                    needs_commit = True

        if needs_commit:
            db.session.commit()
            # Refresh list because some piles may have been merged/deleted
            inventory_piles = Pile.query.filter_by(
                game_token=game_token, owner_id=id
            ).all()

    # 3. Fetch Exits In Grid
    destinations = LocDest.query.filter(
        LocDest.game_token == game_token,
        ((LocDest.loc1_id == id) | 
         ((LocDest.loc2_id == id) & (LocDest.bidirectional == True)))
    ).all()
    grid_exits = []
    for dest in destinations:
        door = dest.door_at(id)
        if door and len(door) == 2:
            if is_in_grid(location, door[0], door[1]):
                target = dest.other_loc(id)
                grid_exits.append({
                    'x': door[0],
                    'y': door[1],
                    'name': target.name,
                    'target_id': target.id
                })

    # 4. Fetch Referenced Items
    referenced_data = []
    for ref in location.item_refs:
        gen_pile = Pile.query.filter_by(
            game_token=game_token, item_id=ref.item.id, owner_id=GENERAL_ID
        ).first()
        referenced_data.append({
            'item': ref.item,
            'quantity': gen_pile.quantity if gen_pile else 0.0
        })

    # 5. Local attributes
    attrib_values = AttribVal.query.filter_by(
        game_token=game_token, subject_id=id
    ).all()

    # 6. Active Character Setup
    active_char_id = request.args.get('active_char_id', type=int)
    if not active_char_id and characters_here:
        active_char_id = characters_here[0].id

    return render_template(
        'play/location.html',
        location=location,
        has_grid=has_grid,
        inventory_piles=inventory_piles,
        characters_here=characters_here,
        destinations=destinations,
        grid_exits=grid_exits,
        referenced_items=referenced_data,
        attrib_values=attrib_values,
        active_char_id=active_char_id,
        context_char=current_char,
        link_letters=LinkLetters(excluded='ctmoe')
    )

@play_bp.route('/play/char/<int:id>')
def play_character(id):
    game_token = g.game_token
    character = Character.query.get_or_404((game_token, id))
    capture_origin(name=character.name)
    exit_loc_id = request.args.get('auto_select_exit', type=int)
    session['old_char_id'] = id
    session.pop('old_loc_id', None)
    
    # Identify other party members at this location
    party_members = []
    if character.travel_party:
        party_members = Character.query.filter(
            Character.game_token == game_token,
            Character.location_id == character.location_id,
            Character.travel_party == character.travel_party,
            Character.id != character.id
        ).all()

    # Fetch piles (Items carried)
    inventory = Pile.query.filter_by(
        game_token=game_token, owner_id=id
    ).all()
    
    # Fetch Attributes
    attrib_values = AttribVal.query.filter_by(
        game_token=game_token, subject_id=id
    ).all()
    
    # Fetch Navigation (Nearby Destinations)
    destinations, has_nonadjacent = get_available_destinations(character)
    
    # Fetch Abilities (Events linked to this character)
    # Assuming a relationship 'abilities' exists in the Character model
    # Or query EventRegistry/Triggers
    abilities = Event.query.filter_by(game_token=game_token, toplevel=True).all() # Placeholder logic

    return render_template(
        'play/character.html',
        character=character,
        inventory=inventory,
        attrib_values=attrib_values,
        destinations=destinations,
        exit_loc_id=exit_loc_id,
        has_nonadjacent=has_nonadjacent,
        abilities=abilities,
        party_members=party_members,
        link_letters=LinkLetters(excluded='gltmoe')
    )

@play_bp.route('/char/<int:id>/drop', methods=['POST'])
def drop_item(id):
    req = RequestHelper('form')
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
def pickup_item(id):
    req = RequestHelper('form')
    item_id = req.get_int('item_id')
    qty = req.get_float('quantity')
    pos = parse_coords(req.get_str('pos'))
    
    char = Character.query.get((g.game_token, id))
    loc = char.location
    
    # Position Dependency Check
    if loc.dimensions and loc.dimensions[0] > 0:
        if not is_adjacent(char.position, pos):
            return jsonify({
                "status": "error", 
                "message": "You are too far away to pick that up."
            }), 400

    # Transfer from Location to Char
    success = transfer_item(
        item_id, from_owner_id=char.location_id, to_owner_id=id,
        quantity=qty, from_pos=pos
    )
    
    if success:
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@play_bp.route('/char/<int:id>/give', methods=['POST'])
def give_item(id):
    req = RequestHelper('form')
    item_id = req.get_int('item_id')
    target_char_id = req.get_int('target_char_id')
    qty = req.get_float('quantity')
    
    char = Character.query.get((g.game_token, id))
    target_char = Character.query.get((g.game_token, target_char_id))
    loc = char.location
    
    # Position Dependency Check
    if loc.dimensions and loc.dimensions[0] > 0:
        if not is_adjacent(char.position, target_char.position):
            return jsonify({
                "status": "error", 
                "message": f"Must be next to {target_char.name} to give items."
            }), 400

    # Transfer from Char to Target Char
    success = transfer_item(
        item_id, from_owner_id=id, to_owner_id=target_char_id,
        quantity=qty
    )

    if success:
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@play_bp.route('/char/<int:id>/equip', methods=['POST'])
def equip_item(id):
    """Assigns an item pile to a specific equipment slot."""
    req = RequestHelper('form')
    game_token = g.game_token
    item_id = req.get_int('item_id')
    slot = req.get_str('slot')
    
    # Store the most recently used slot in the session for UI convenience
    session['default_slot'] = slot
    
    # 1. Fetch character and item to ensure they exist (for the log message)
    char = Character.query.get((game_token, id))
    item = Item.query.get((game_token, item_id))
    
    if not char or not item:
        return jsonify({'status': 'error', 'message': 'Character or Item not found.'}), 404

    # 2. Find the specific pile in the character's inventory
    pile = Pile.query.filter_by(
        game_token=game_token, 
        owner_id=id, 
        item_id=item_id
    ).first()

    if not pile:
        return jsonify({
            'status': 'error',
            'message': f"No {item.name} found in {char.name}'s inventory."
        }), 400

    # 3. Update the slot
    pile.slot = slot
    db.session.commit()

    # 4. Log to Chronicle
    msg = f"{char.name} equipped {item.name} to {slot}."
    add_message(game_token, msg)

    return jsonify({
        'status': 'success',
        'message': msg
    })

@play_bp.route('/char/<int:id>/unequip', methods=['POST'])
def unequip_item(id):
    """Removes an item from its equipment slot, returning it to the general pack."""
    req = RequestHelper('form')
    game_token = g.game_token
    item_id = req.get_int('item_id')
    
    char = Character.query.get((game_token, id))
    item = Item.query.get((game_token, item_id))

    if not char or not item:
        return jsonify({'status': 'error', 'message': 'Character or Item not found.'}), 404

    # Find the pile
    pile = Pile.query.filter_by(
        game_token=game_token, 
        owner_id=id, 
        item_id=item_id
    ).first()

    if not pile:
        return jsonify({
            'status': 'error',
            'message': f"No {item.name} found in {char.name}'s inventory."
        }), 400

    # Remove the slot assignment (set to None/NULL)
    pile.slot = None
    db.session.commit()

    # Log to Chronicle
    msg = f"{char.name} unequipped {item.name}."
    add_message(game_token, msg)

    return jsonify({
        'status': 'success',
        'message': msg
    })

@play_bp.route('/char/move/<int:id>', methods=['POST'])
def char_move(id):
    req = RequestHelper('form')
    dx = req.get_int('dx')
    dy = req.get_int('dy')
    move_party = req.get_bool('move_party')
    
    success, results = move_group(id, dx, dy, move_party)
    if success:
        return jsonify({"status": "success", "positions": results})
    return jsonify({"status": "error", "message": results}), 400

@play_bp.route('/char/go/<int:id>', methods=['POST'])
def char_travel(id):
    req = RequestHelper('form')
    dest_loc_id = req.get_int('dest_id')
    move_party = req.get_bool('move_party')
    success, message = arrive_at_destination(id, dest_loc_id, move_party)
    if success:
        return jsonify({"status": "arrived"})
    else:
        return jsonify({"status": "error", "message": message}), 400

# ------------------------------------------------------------------------
# Item & Pile Routes
# ------------------------------------------------------------------------

@play_bp.route('/play/item/<int:id>')
def play_item(id):
    req = RequestHelper('args')
    game_token = g.game_token
    
    item = Item.query.get_or_404((game_token, id))
    capture_origin(name=item.name)

    # RESOLVE THE OWNER (The viewed pile)
    owner_id = request.args.get('owner_id', type=int)
    if not owner_id:
        # Default fallback based on how the item is stored
        if item.storage_type == StorageType.UNIVERSAL:
            owner_id = GENERAL_ID
        elif session.get('old_char_id'):
            owner_id = session.get('old_char_id')
        else:
            owner_id = session.get('old_loc_id') or GENERAL_ID

    owner = Entity.query.get((game_token, owner_id))
    
    # INFER ACTIVE CONTEXT FROM OWNER
    char_id = None
    loc_id = None
    if owner.entity_type == Character.TYPENAME:
        # If viewing a character, they are the actor.
        char_id = owner.id
        # Their environment is where they are standing.
        loc_id = owner.location_id
        session['old_char_id'] = char_id
        session['old_loc_id'] = loc_id
    elif owner.entity_type == Location.TYPENAME:
        # If viewing a location, it is the environment.
        loc_id = owner.id
        session['old_loc_id'] = loc_id
        # The actor is whoever was last active or explicitly passed.
        char_id = req.get_int('char_id') or session.get('old_char_id')
        
        # Clear character if they aren't here
        if char_id:
            char = Character.query.get((game_token, char_id))
            if not char or char.location_id != loc_id:
                char_id = None
    else: # General Storage (ID 1)
        # Actor and Loc must come from session/args.
        char_id = req.get_int('char_id') or session.get('old_char_id')
        loc_id = req.get_int('loc_id') or session.get('old_loc_id')

    char = Character.query.get((game_token, char_id)) if char_id else None
    loc = Location.query.get((game_token, loc_id)) if loc_id else None

    # Fetch the specific Pile record
    query = Pile.query.filter_by(
        game_token=game_token, 
        item_id=id, 
        owner_id=owner.id
    )
    pos = None
    raw_pos = request.args.getlist('pos[]')
    if raw_pos:
        pos = tuple(int(x) for x in raw_pos)
        query = query.filter_by(position=pos)
    pile = query.first()

    # If no pile exists yet, create a virtual one for the UI
    if not pile:
        pile = Pile(item_id=id, owner_id=owner.id, quantity=0.0, position=pos)

    # Collect all relevant entities for attribute checking
    all_relevant_entities = set()
    if owner.id != GENERAL_ID:
        all_relevant_entities.add(owner.id)
    if char_id:
        all_relevant_entities.add(char_id)
    if loc_id:
        all_relevant_entities.add(loc_id)

    # Recipes that PRODUCE this item
    # Enriched allows UI to show 🚫 icon and specific reason tooltip.
    all_attrib_vals = AttribVal.query.filter(
        AttribVal.game_token == game_token,
        AttribVal.subject_id.in_(all_relevant_entities)
    ).all()
    attribval_lookup = {(av.subject_id, av.attrib_id): av for av in all_attrib_vals}

    enriched_recipes = []
    for r in item.recipes:
        best_host_id = find_best_host(r, char_id, loc_id)
        
        # Determine viability for the UI (Greedy check)
        # We check the 'best_host_id' if found, otherwise we check the active character
        ui_host_id = best_host_id or char_id or loc_id or GENERAL_ID
        can_do, reason = can_perform_recipe(ui_host_id, r, loc_id=loc_id)

        resolved_ingredients = resolve_recipe_sources(
            ui_host_id, r, loc_id=loc_id)
        
        sources_ui_data = []
        for res in resolved_ingredients:
            url_params = { 'owner_id': res['anticipated_owner_id'] }
            if char_id and res['anticipated_owner_type'] != Character.TYPENAME:
                url_params['char_id'] = char_id
            elif loc_id and res['anticipated_owner_id'] == GENERAL_ID:
                url_params['loc_id'] = loc_id
            if res['representative_pile'] and res['representative_pile'].position:
                url_params['pos[]'] = res['representative_pile'].position

            sources_ui_data.append({
                'ingredient': res['item'],
                'q_required': res['source_def'].q_required,
                'preserve': res['source_def'].preserve,
                'current_stock': res['total_available'],
                'pile_owner_id': res['anticipated_owner_id'],
                'pile_owner_type': res['anticipated_owner_type'],
                'url_params': url_params
            })

        # Check attribute requirements against all relevant entities
        relevant_entities = set()
        if owner.id != GENERAL_ID:
            relevant_entities.add(owner.id)
        if char_id:
            relevant_entities.add(char_id)
        if loc_id:
            relevant_entities.add(loc_id)
        
        # Add any entities that provide ingredients
        for res in resolved_ingredients:
            if res['anticipated_owner_id'] != GENERAL_ID:
                relevant_entities.add(res['anticipated_owner_id'])
                all_relevant_entities.add(res['anticipated_owner_id'])
        
        attrib_reqs_ui_data = []
        for req in r.attrib_reqs:
            req_met = False
            current_val = 0.0
            satisfying_entity = None
            entity_with_value = None
            
            for entity_id in relevant_entities:
                av = attribval_lookup.get((entity_id, req.attrib_id), 0.0)
                if av:
                    if req.in_range(av.value):
                        req_met = True
                        current_val = av.value
                        satisfying_entity = entity_id
                        break
                    elif av.value > current_val:
                        current_val = av.value
                        entity_with_value = entity_id
            
            # Use satisfying entity if requirement met, otherwise entity with highest value
            link_entity_id = satisfying_entity or entity_with_value
            
            attrib_reqs_ui_data.append({
                'attrib': req.attrib,
                'min_val': req.min_val,
                'max_val': req.max_val,
                'current_val': current_val,
                'is_satisfied': req_met,
                'link_entity_id': link_entity_id
            })

        host_ent = Entity.query.get((game_token, best_host_id)) if best_host_id else None

        enriched_recipes.append({
            'id': r.id,
            'host_id': best_host_id,
            'host_name': host_ent.name if host_ent else "No Host",
            'rate_amount': r.rate_amount,
            'rate_duration': r.rate_duration,
            'instant': r.instant,
            'can_produce': can_do,
            'reason': reason,
            'sources': sources_ui_data,
            'attrib_reqs': attrib_reqs_ui_data
        })

    for r_data in enriched_recipes:
        recipe_obj = next(r for r in item.recipes if r.id == r_data['id'])
        
        # Find the limiting ingredient
        possible_batches = []
        for source in r_data['sources']:
            if not source['preserve'] and source['q_required'] > 0:
                batches = int(source['current_stock'] // source['q_required'])
                possible_batches.append(batches)
        
        # If it's infinite/no ingredients, default to a sensible max or 1
        max_val = min(possible_batches) if possible_batches else 1
        # Ensure at least 1 is shown if they can produce, otherwise 0
        r_data['max_batches'] = max(1, max_val) if r_data['can_produce'] else 0

    # Recipes where this item is a SOURCE (Ingredient)
    url_params = {}
    if char_id:
        url_params['char_id'] = char_id
    elif loc_id:
        url_params['loc_id'] = loc_id

    used_for_production = []
    for source_link in item.as_ingredient:
        product = source_link.recipe.product
        if product.id != id:
            used_for_production.append({
                'item': product,
                'q_required': source_link.q_required,
                'preserve': source_link.preserve,
                'url_params': url_params
            })

    # Recipes where this item is a BYPRODUCT
    byproduct_of = []
    for byproduct_link in item.as_byproducts:
        product = byproduct_link.recipe.product
        byproduct_of.append({
            'item': product,
            'rate_amount': byproduct_link.rate_amount,
            'url_params': url_params
        })

    # Check for active progress
    current_progress = Progress.query.filter_by(
        game_token=game_token, product_id=id
    ).first()

    # Characters nearby (for the "Give" button)
    other_chars_here = []
    if isinstance(owner, Character) and owner.location_id:
        raw_chars = Character.query.filter(
            Character.game_token == game_token,
            Character.location_id == owner.location_id,
            Character.id != owner.id
        ).all()
        
        # Enrich the character objects with proximity data for the UI
        for c in raw_chars:
            c.is_reachable = is_adjacent(
                owner.position, c.position) if owner.position else True
            other_chars_here.append(c)

    # Determine if the item is physically reachable by the context character
    is_reachable = True
    reach_error = None
    if char:
        # If the item is on the ground at a location with a grid
        if owner.entity_type == 'location' and owner.dimensions and owner.dimensions[0] > 0:
            if pile.position:
                if not is_adjacent(char.position, pile.position):
                    is_reachable = False
                    reach_error = "Must be next to item to pick up."
            else:
                # Item is at location but has no position (internal storage/unplaced)
                # In a strict grid game, unplaced items might be unreachable
                is_reachable = False
                reach_error = "This item is not currently placed on the grid."
        
        # If the item is carried by another character
        elif owner.entity_type == Character.TYPENAME and owner.id != char.id:
            if owner.location_id == char.location_id:
                if not is_adjacent(char.position, owner.position):
                    is_reachable = False
                    reach_error = f"Must be next to {owner.name} to trade."
            else:
                is_reachable = False
                reach_error = f"{owner.name} is in a different location."

    # For equipping to a slot
    overall = Overall.query.get(game_token)

    # Create entities lookup for attribute requirement links
    attribreq_entities = {owner.id: owner}
    if char:
        attribreq_entities[char.id] = char
    if loc:
        attribreq_entities[loc.id] = char
    for entity_id in all_relevant_entities:
        if entity_id not in attribreq_entities and entity_id != GENERAL_ID:
            entity = Entity.query.get((game_token, entity_id))
            if entity:
                attribreq_entities[entity_id] = entity

    return render_template(
        'play/item.html',
        item=item,
        owner=owner,
        pile=pile,
        char=char,
        loc=loc,
        recipes=enriched_recipes,
        used_for_production=used_for_production,
        byproduct_of=byproduct_of,
        progress=current_progress,
        available_slots=overall.slots,
        other_chars_here=other_chars_here,
        is_reachable=is_reachable,
        reach_error=reach_error,
        attribreq_entities=attribreq_entities,
        link_letters=LinkLetters(excluded='moedpqrg')
    )

# ------------------------------------------------------------------------
# Production Routes
# ------------------------------------------------------------------------

@play_bp.route('/production/status/host/<int:host_id>')
def production_status(host_id):
    """
    Heartbeat endpoint to calculate current progress and refresh recipe availability.
    Returns status for ALL possible hosts of this item (General, Character, Location).
    """
    game_token = g.game_token
    req = RequestHelper('args')
    item_id = req.get_int('item_id')
    owner_id = req.get_int('owner_id')

    # 1. TICK THE WORLD
    # This keeps all hosts (System, Suzy, Bob) in sync.
    halt_messages = tick_all_active(host_id)

    # 2. GATHER PAGE DATA
    # Resolve main item and focus progress
    main_item = Item.query.get((game_token, item_id))
    if not main_item:
        return jsonify({"error": "Item not found"}), 404

    main_pile = Pile.query.filter_by(
        game_token=game_token, owner_id=owner_id, item_id=item_id).first()
    
    host_prog = Progress.query.filter_by(
        game_token=game_token, host_id=host_id).first()

    # 3. GATHER RECIPES & INGREDIENT TOTALS
    recipe_data = []
    source_quantities = {}
    attrib_data = []

    for r in main_item.recipes:
        # Can this specific worker produce this at this location?
        can_do, reason = can_perform_recipe(host_id, r, 1, owner_id)
        recipe_data.append({
            "recipe_id": r.id, "can_produce": can_do, "reason": reason
        })

        # Where are the ingredients relative to this worker and location?
        resolved = resolve_recipe_sources(host_id, r, owner_id)
        for res in resolved:
            s_item = res['item']
            source_quantities[s_item.id] = format_num(res['total_available'])

        # Collect attribute values used in these recipes
        for req_attr in r.attrib_reqs:
            # Check host, owner, and context for this attribute
            for eid in set([host_id, owner_id, GENERAL_ID]):
                av = AttribVal.query.filter_by(game_token=game_token, subject_id=eid, attrib_id=req_attr.attrib_id).first()
                if av:
                    attrib_data.append({
                        "attrib_id": av.attrib_id, 
                        "subject_id": av.subject_id, 
                        "value": format_num(av.value)
                    })

    # 4. GATHER "USED TO PRODUCE" DATA
    # Assume same owner/context as current page.
    used_for_data = []
    for source_link in main_item.as_ingredient:
        product = source_link.recipe.product
        used_for_data.append({
            "id": product.id,
            "q_required": source_link.q_required,
            "preserve": source_link.preserve
        })

    return jsonify({
        "main": {
            "quantity": format_num(main_pile.quantity if main_pile else 0),
            "is_ongoing": bool(host_prog and host_prog.is_ongoing),
            "active_recipe_id": host_prog.recipe_id if host_prog else None,
            "batches": host_prog.batches_processed if host_prog else 0,
            "start_time": host_prog.start_time.isoformat() if (
                host_prog and host_prog.start_time) else None,
            "rate_duration": host_prog.recipe.rate_duration if (
                host_prog and host_prog.is_ongoing) else None
        },
        "sources": [
            {"id": sid, "quantity": sqty}
            for sid, sqty in source_quantities.items()],
        "used_for": used_for_data,
        "attribs": attrib_data,
        "recipes": recipe_data,
        "halt_messages": halt_messages
    })

@play_bp.route('/production/status/item/<int:item_id>')
def item_production_status(item_id):
    """
    Heartbeat for the item page. 
    Returns status for ALL possible hosts of this item (General, Character, Location).
    """
    game_token = g.game_token
    # Tick everything to ensure numbers are fresh
    tick_all_active()

    # Find all ongoing progress for this specific product
    progress_records = Progress.query.filter_by(
        game_token=game_token, product_id=item_id, is_ongoing=True).all()

    # Format for JS consumption
    status_map = {}
    for p in progress_records:
        status_map[p.host_id] = {
            "recipe_id": p.recipe_id,
            "batches": p.batches_processed,
            "start_time": p.start_time.isoformat() if p.start_time else None,
            "rate_duration": p.recipe.rate_duration
        }

    return jsonify(status_map)

@play_bp.route('/production/start/host/<int:host_id>', methods=['POST'])
def start_item_production(host_id):
    game_token = g.game_token
    req = RequestHelper('form')
    recipe_id = req.get_int('recipe_id')
    owner_id = req.get_int('owner_id')

    owner = Entity.query.get((game_token, owner_id))
    char_id = owner_id if owner.entity_type == Character.TYPENAME else None
    loc_id = owner_id if owner.entity_type == Location.TYPENAME else None

    success, message = start_production(host_id, recipe_id, char_id, loc_id)
    return jsonify({
        "status": "success" if success else "error", 
        "message": message
    })

@play_bp.route('/production/stop/host/<int:host_id>', methods=['POST'])
def stop_item_production(host_id):
    if stop_production(host_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@play_bp.route('/production/instant/host/<int:host_id>', methods=['POST'])
def instant_item_production(host_id):
    req = RequestHelper('form')
    owner_id = req.get_int('owner_id')
    recipe_id = req.get_int('recipe_id')
    num_batches = req.get_int('batches')

    recipe = Recipe.query.get((g.game_token, recipe_id))
    if not recipe:
        return jsonify({"status": "error", "message": "Recipe not found."})

    # Perform production
    actual_done, halt_reason = execute_production(
        host_id, recipe, num_batches, owner_id)
    
    if actual_done > 0:
        db.session.commit()
        msg = f"Obtained {actual_done} batch{'es' if actual_done > 1 else ''}."
        if halt_reason:
            msg += f" Stopped early: {halt_reason}"
        return jsonify({"status": "success", "message": msg})
    
    return jsonify({"status": "error", "message": halt_reason or "Production failed."})

# ------------------------------------------------------------------------
# Events & Dice
# ------------------------------------------------------------------------

@play_bp.route('/play/event/<int:id>', methods=['GET'])
def play_event(id):
    game_token = g.game_token
    event = Event.query.get_or_404((game_token, id))
    capture_origin(name=event.name)
    
    # Get Context (Who is acting?)
    subject = None
    subject_id = request.args.get('subject_id', type=int)
    if subject_id:
        subject = Entity.query.filter_by(
            game_token=game_token, id=subject_id).first_or_404()

    # Analyze requirements
    needs_other1 = any(d.role == Participant.OTHER1 for d in event.determinants)
    needs_other2 = any(d.role == Participant.OTHER2 for d in event.determinants)
    
    # Identify Item Selectors
    # If ChildItem is True but no item_id is configured, the user must choose.
    # Or if there are multiple piles available for that item.
    item_selectors = {}
    for d in event.determinants:
        if d.child_of_anchor:
            # Add to a dict to ensure we only show one dropdown per role
            if not d.item_id:
                item_selectors[d.role] = {
                    'label': d.label or "Item",
                    'options': get_inventory_for_role(d.role) 
                }
    
    # Fetch Eligible Participants (for the dropdowns)
    # We query the base Entity table because ANY entity might have the required attribute
    eligible_sources = Entity.query.filter_by(game_token=game_token).all()
    eligible_targets = eligible_sources # Simplified
    
    # Pre-calculate Determinants (Modifiers)
    # This logic helps the UI show things like "Strength (+5)" before rolling
    determinants = []
    if subject_id:
        for det in getattr(event, 'determinants', []):
            val = get_entity_value(subject_id, det)
            determinants.append({
                'label': det.label,
                'operation': det.operation,
                'modifier': det.modifier,
                'scaling': det.scaling,
                'value': val
            })

    return render_template(
        'play/event.html',
        event=event,
        subject=subject,
        needs_other1=needs_other1,
        needs_other2=needs_other2,
        item_selectors=item_selectors,
        eligible_sources=eligible_sources,
        eligible_targets=eligible_targets,
        determinants=determinants,
        operation=Operation,
        link_letters=LinkLetters(excluded='moer')
    )

@play_bp.route('/event/preview/<int:id>')
def event_preview(id):
    """AJAX helper to set up determinants."""
    game_token = g.game_token
    event = Event.query.get((game_token, id))
    
    context = {
        'subj_id': request.args.get('subj_id', type=int),
        '2nd_id': request.args.get('sec_id', type=int),
        '3rd_id': request.args.get('tert_id', type=int),
        'univ_id': GENERAL_ID
    }
    
    modifiers = calculate_determinants(event, context)
    return jsonify(modifiers)

@play_bp.route('/event/roll/<int:id>', methods=['POST'])
def roll_event(id):
    game_token = g.game_token
    event = Event.query.get_or_404((game_token, id))
    req = RequestHelper('form')

    # Gather context IDs
    context_ids = {
        'subj_id': req.get_int('subj_id'),
        '2nd_id': req.get_int('sec_id'),
        '3rd_id': req.get_int('tert_id'),
        'univ_id': GENERAL_ID
    }
    
    # Add any item instance selections
    for key in req:
        if key.endswith('_item_id'):
            context_ids[key] = req.get_int(key)

    if event.outcome_type == OutcomeType.ROLLER:
        n_dice = request.form.get('num_dice', 1, type=int)
        sides = request.form.get('sides', 20, type=int)
        bonus = request.form.get('bonus', 0, type=int)
        result_num, result_str = roll_for_system_outcome(
            id, n_dice, sides, bonus)
    else:
        difficulty = request.form.get('difficulty', 0.55, type=float)
        result_num, result_str = roll_for_outcome(
            id, context_ids, difficulty)
    
    return jsonify({
        "result_value": result_num,
        "display": result_str
    })

@play_bp.route('/event/apply/<int:id>', methods=['POST'])
def apply_event(id):
    """Apply a roll result to a specific container."""
    # 1. The thing we are changing (The Key)
    req = RequestHelper('form')
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
    
    val_record = AttribVal.query.filter_by(
        game_token=game_token, attrib_id=attrib_id, subject_id=subject_id
    ).first()
    if not val_record:
        val_record = AttribVal(
            game_token=game_token, attrib_id=attrib_id,
            subject_id=subject_id, value=0.0)
        db.session.add(val_record)

    if request.method == 'POST':
        req = RequestHelper('form')
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
        return redirect_back()

    # Get reverse dependencies (items needing this for recipes)
    items_requiring_this = Item.query.join(Recipe).join(RecipeAttribReq).filter(
        RecipeAttribReq.attrib_id == attrib_id,
        Item.game_token == game_token
    ).all()

    return render_template(
        'play/attrib.html', 
        attribute=attribute, 
        subject=subject, 
        attrib_value=val_record,
        items_requiring_this=items_requiring_this,
        link_letters=LinkLetters(excluded='moesct'))
