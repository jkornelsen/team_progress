import logging
from flask import (
    Blueprint, render_template, request, jsonify, g, session, current_app)
from http import HTTPStatus
from sqlalchemy.orm import joinedload
from app.models import (
    db, Entity, Item, Character, Location, Attrib, Event,
    Pile, AttribVal, Operation, OutcomeType,
    Recipe, RecipeAttribReq, LocDest,
    Progress, Overall, WinRequirement, GameMessage,
    GENERAL_ID, StorageType, Participant)
from app.utils import (
    RequestHelper, ContextIds, format_num, parse_coords, LinkLetters,
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

# ------------------------------------------------------------------------
# The Overview (Dashboard)
# ------------------------------------------------------------------------

@play_bp.route('/overview')
def overview():
    game_token = g.game_token
    
    # Fetch Top-Level Entities
    chars = Character.query.filter_by(game_token=game_token, toplevel=True).all()
    locs = Location.query.filter_by(game_token=game_token, toplevel=True).all()
    items = Item.query.filter_by(game_token=game_token, toplevel=True).all()
    events = Event.query.filter_by(game_token=game_token, toplevel=True).all()
    
    # Tick All Production
    tick_all_active()

    # Fetch IDs of items currently being produced by the General Host
    general_production_ids = {
        p.product_id for p in Progress.query.filter_by(
            game_token=game_token, 
            host_id=GENERAL_ID
        ).all()
    }

    # Check Win Requirements
    win_reqs, all_met = validate_requirements(game_token)
    overall = Overall.query.get(game_token)
    
    # Recent Messages
    messages = GameMessage.query.filter_by(game_token=game_token)\
                .order_by(GameMessage.timestamp.desc()).limit(30).all()
    messages.reverse()

    return render_template(
        'play/overview.html',
        characters=chars,
        locations=locs,
        items=items,
        general_production_ids=general_production_ids,
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
    referenced_data = sorted(referenced_data, key=lambda x: x['item'].name)

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
        ctx_char=current_char,
        link_letters=LinkLetters(excluded='ctmoed')
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
    
    return render_template(
        'play/character.html',
        character=character,
        inventory=inventory,
        attrib_values=attrib_values,
        destinations=destinations,
        exit_loc_id=exit_loc_id,
        has_nonadjacent=has_nonadjacent,
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
    
    item = Item.query.get((g.game_token, item_id))
    if success:
        db.session.commit()
        add_message(f"{char.name} dropped {qty} {item.name}")
        return '', HTTPStatus.NO_CONTENT
    return jsonify(
        {"message": f"Could not drop {item.name}."}
    ), HTTPStatus.BAD_REQUEST

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
                "message": "You are too far away to pick that up."
            }), HTTPStatus.BAD_REQUEST

    # Transfer from Location to Char
    success = transfer_item(
        item_id, from_owner_id=char.location_id, to_owner_id=id,
        quantity=qty, from_pos=pos
    )
    
    item = Item.query.get((g.game_token, item_id))
    if success:
        db.session.commit()
        add_message(f"{char.name} picked up {qty} {item.name}")
        return '', HTTPStatus.NO_CONTENT

    return jsonify(
        {"message": "{char.name} could not pick up {item.name}."}
    ), HTTPStatus.BAD_REQUEST

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
                "message": f"Must be next to {target_char.name} to give items."
            }), HTTPStatus.BAD_REQUEST

    # Transfer from Char to Target Char
    success = transfer_item(
        item_id, from_owner_id=id, to_owner_id=target_char_id,
        quantity=qty
    )

    item = Item.query.get((g.game_token, item_id))
    if success:
        db.session.commit()
        add_message(f"{char.name} gave {qty} {item.name} to {target_char.name}")
        return '', HTTPStatus.NO_CONTENT
    return jsonify(
        {"message": "Could not transfer {item.name} to {target_char.name}."}
    ), HTTPStatus.BAD_REQUEST

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
        return jsonify(
            {'message': 'Character or Item not found.'}), HTTPStatus.NOT_FOUND

    # 2. Find the specific pile in the character's inventory
    pile = Pile.query.filter_by(
        game_token=game_token, 
        owner_id=id, 
        item_id=item_id
    ).first()

    if not pile:
        return jsonify({
            'message': f"No {item.name} found in {char.name}'s inventory."
        }), HTTPStatus.BAD_REQUEST

    # 3. Update the slot
    pile.slot = slot
    db.session.commit()

    # 4. Log
    add_message(f"{char.name} equipped {item.name} to {slot}")
    return '', HTTPStatus.NO_CONTENT

@play_bp.route('/char/<int:id>/unequip', methods=['POST'])
def unequip_item(id):
    """Removes an item from its equipment slot, returning it to the general pack."""
    req = RequestHelper('form')
    game_token = g.game_token
    item_id = req.get_int('item_id')
    
    char = Character.query.get((game_token, id))
    item = Item.query.get((game_token, item_id))

    if not char or not item:
        return jsonify(
            {'message': 'Character or Item not found.'}), HTTPStatus.NOT_FOUND

    # Find the pile
    pile = Pile.query.filter_by(
        game_token=game_token, 
        owner_id=id, 
        item_id=item_id
    ).first()

    if not pile:
        return jsonify({
            'message': f"No {item.name} found in {char.name}'s inventory."
        }), HTTPStatus.BAD_REQUEST

    # Remove the slot assignment (set to None/NULL)
    pile.slot = None
    db.session.commit()

    # Log
    add_message(f"{char.name} unequipped {item.name}")
    return '', HTTPStatus.NO_CONTENT

@play_bp.route('/char/<int:id>/move', methods=['POST'])
def char_move(id):
    req = RequestHelper('form')
    dx = req.get_int('dx')
    dy = req.get_int('dy')
    move_party = req.get_bool('move_party')
    
    success, results = move_group(id, dx, dy, move_party)
    if success:
        return jsonify({"positions": results}), HTTPStatus.OK
    return jsonify({"message": results}), HTTPStatus.BAD_REQUEST

@play_bp.route('/char/<int:id>/go', methods=['POST'])
def char_travel(id):
    req = RequestHelper('form')
    dest_loc_id = req.get_int('dest_id')
    move_party = req.get_bool('move_party')
    success, message = arrive_at_destination(id, dest_loc_id, move_party)
    if success:
        return '', HTTPStatus.NO_CONTENT
    return jsonify({"message": message}), HTTPStatus.BAD_REQUEST

# ------------------------------------------------------------------------
# Item & Pile Routes
# ------------------------------------------------------------------------

@play_bp.route('/play/item/<int:id>')
def play_item(id):
    game_token = g.game_token
    req = RequestHelper('args')

    item = Item.query.get_or_404((game_token, id))
    capture_origin(name=item.name)

    # Determine Viewed Pile Owner
    # This is the pile displayed at the top of the page.
    owner_id = req.get_int('owner_id')
    if not owner_id:
        # Default fallback based on how the item is stored
        if item.storage_type == StorageType.UNIVERSAL:
            owner_id = GENERAL_ID
        elif session.get('old_char_id'):
            owner_id = session.get('old_char_id')
        else:
            owner_id = session.get('old_loc_id') or GENERAL_ID

    owner = Entity.query.get((game_token, owner_id))
    
    # Capture and Clean Context
    ctx = ContextIds(
        owner_id,
        req.get_int('char_id') or session.get('old_char_id'),
        req.get_int('loc_id') or session.get('old_loc_id')
    )
    if owner.entity_type == Character.TYPENAME:
        ctx.char_id = owner.id
        session['old_char_id'] = owner.id
    elif owner.entity_type == Location.TYPENAME:
        ctx.loc_id = owner.id
        session['old_loc_id'] = owner.id
        if ctx.char_id:
            # Clear character if they aren't here
            char = Character.query.get((game_token, ctx.char_id))
            if not char or char.location_id != owner.id:
                ctx.char_id = None
                del session['old_char_id']

    ctx_char = Character.query.get((game_token, ctx.char_id)) if ctx.char_id else None
    if ctx_char:
        ctx.loc_id = ctx_char.location_id
        session['old_loc_id'] = ctx_char.location_id
    ctx_loc = Location.query.get((game_token, ctx.loc_id)) if ctx.loc_id else None

    logger.debug(
        f"---- play_item() ----\n"
        f"Item:{item.id} | Owner:{owner.id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

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

    # If no pile exists yet, create a virtual one
    if not pile:
        pile = Pile(item_id=id, owner_id=owner.id, quantity=0.0, position=pos)

    # Collect all relevant entities for attribute checking
    all_attribreq_entity_ids = set()
    if owner.id != GENERAL_ID:
        all_attribreq_entity_ids.add(owner.id)
    if ctx.addl_char_id:
        all_attribreq_entity_ids.add(ctx.char_id)
    if ctx.addl_loc_id:
        all_attribreq_entity_ids.add(ctx.loc_id)

    # Recipes that PRODUCE this item
    # Enriched allows UI to show 🚫 icon and specific reason tooltip.
    all_attrib_vals = AttribVal.query.filter(
        AttribVal.game_token == game_token,
        AttribVal.subject_id.in_(all_attribreq_entity_ids)
    ).all()
    attribval_lookup = {(av.subject_id, av.attrib_id): av for av in all_attrib_vals}

    enriched_recipes = []
    for r in item.recipes:
        host_id = find_best_host(r, owner.id, ctx)
        logger.debug(f'host {host_id}')
        
        # Determine viability for the UI (Greedy check)
        can_do, reason = can_perform_recipe(host_id, r, owner.id, ctx)
        resolved_ingredients = resolve_recipe_sources(host_id, r, ctx)
        
        sources_ui_data = []
        for res in resolved_ingredients:
            url_params = { 'owner_id': res['anticipated_owner_id'] }
            if ctx.addl_char_id and res['anticipated_owner_type'] != Character.TYPENAME:
                url_params['char_id'] = ctx.char_id
            elif ctx.addl_loc_id and res['anticipated_owner_id'] == GENERAL_ID:
                url_params['loc_id'] = ctx.loc_id
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
        attribreq_entity_ids = set()
        if owner.id != GENERAL_ID:
            attribreq_entity_ids.add(owner.id)
        if ctx.addl_char_id:
            attribreq_entity_ids.add(ctx.char_id)
        if ctx.addl_loc_id:
            attribreq_entity_ids.add(ctx.loc_id)
        
        # Add any entities that provide ingredients
        for res in resolved_ingredients:
            if res['anticipated_owner_id'] != GENERAL_ID:
                attribreq_entity_ids.add(res['anticipated_owner_id'])
                all_attribreq_entity_ids.add(res['anticipated_owner_id'])
        
        attrib_reqs_ui_data = []
        for req in r.attrib_reqs:
            req_met = False
            current_val = 0.0
            satisfying_entity = None
            entity_with_value = None
            
            for entity_id in attribreq_entity_ids:
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

        host_ent = Entity.query.get((game_token, host_id)) if host_id else None

        enriched_recipes.append({
            'id': r.id,
            'host_id': host_id,
            'host_name': host_ent.name if host_ent else "No Host",
            'is_location_hosted': r.is_location_hosted,
            'product_id': r.product_id,
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
    used_for_production = []
    for source_link in item.as_ingredient:
        product = source_link.recipe.product
        if product.id != id:
            used_for_production.append({
                'item': product,
                'q_required': source_link.q_required,
                'preserve': source_link.preserve,
                'url_params': ctx.get_params()
            })

    # Recipes where this item is a BYPRODUCT
    byproduct_of = []
    for byproduct_link in item.as_byproducts:
        product = byproduct_link.recipe.product
        byproduct_of.append({
            'item': product,
            'rate_amount': byproduct_link.rate_amount,
            'url_params': ctx.get_params()
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
    if ctx_char:
        # If the item is on the ground at a location with a grid
        if owner.entity_type == 'location' and owner.dimensions and owner.dimensions[0] > 0:
            if pile.position:
                if not is_adjacent(ctx_char.position, pile.position):
                    is_reachable = False
                    reach_error = "Must be next to item to pick up."
            else:
                # Item is at location but has no position (internal storage/unplaced)
                # In a strict grid game, unplaced items might be unreachable
                is_reachable = False
                reach_error = "This item is not currently placed on the grid."
        
        # If the item is carried by another character
        elif owner.entity_type == Character.TYPENAME and owner.id != ctx_char.id:
            if owner.location_id == ctx_char.location_id:
                if not is_adjacent(ctx_char.position, owner.position):
                    is_reachable = False
                    reach_error = f"Must be next to {owner.name} to trade."
            else:
                is_reachable = False
                reach_error = f"{owner.name} is in a different location."

    # Attributes for this Item
    attrib_values = AttribVal.query.filter_by(
        game_token=game_token, subject_id=id
    ).all()

    # For equipping to a slot
    overall = Overall.query.get(game_token)

    # Create entities lookup for attribute requirement links
    attribreq_entities = {owner.id: owner}
    if ctx_char:
        attribreq_entities[ctx_char.id] = ctx_char
    if ctx_loc:
        attribreq_entities[ctx_loc.id] = ctx_char
    for entity_id in all_attribreq_entity_ids:
        if entity_id not in attribreq_entities and entity_id != GENERAL_ID:
            entity = Entity.query.get((game_token, entity_id))
            if entity:
                attribreq_entities[entity_id] = entity

    return render_template(
        'play/item.html',
        item=item,
        owner=owner,
        pile=pile,
        ctx_char=ctx_char,
        ctx_loc=ctx_loc,
        recipes=enriched_recipes,
        used_for_production=used_for_production,
        byproduct_of=byproduct_of,
        progress=current_progress,
        attribreq_entities=attribreq_entities,
        available_slots=overall.slots,
        other_chars_here=other_chars_here,
        attrib_values=attrib_values,
        is_reachable=is_reachable,
        reach_error=reach_error,
        link_letters=LinkLetters(excluded='moedpqrg')
    )

# ------------------------------------------------------------------------
# Production Routes
# ------------------------------------------------------------------------

@play_bp.route('/production/status/item/<int:item_id>/owner/<int:owner_id>', methods=['POST'])
def item_production_status(item_id, owner_id):
    """
    Heartbeat endpoint to calculate current progress and refresh recipe availability.
    """
    game_token = g.game_token
    req = RequestHelper('form')
    
    # Contextual IDs
    char_id = req.get_int('char_id')
    loc_id = req.get_int('loc_id')
    ctx = ContextIds(owner_id, char_id, loc_id)

    logger.debug(
        f"---- item_production_status() ----\n"
        f"Item:{item_id} | Owner:{owner_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    # 1. TICK THE WORLD
    tick_all_active()

    # 2. GATHER DATA FOR THE SPECIFIC PILE WE ARE VIEWING
    main_item = Item.query.get((game_token, item_id))
    if not main_item:
        return jsonify({"message": "Item not found"}), HTTPStatus.NOT_FOUND

    main_pile = Pile.query.filter_by(
        game_token=game_token, owner_id=owner_id, item_id=item_id).first()
    
    # 3. GATHER PROGRESS FOR ALL POSSIBLE HOSTS
    # We check if any of our context entities are currently making this item
    potential_hosts = [GENERAL_ID, char_id, loc_id]
    all_progs = Progress.query.filter_by(
        game_token=game_token, 
        product_id=item_id
    ).filter(
        Progress.host_id.in_([h for h in potential_hosts if h])
    ).all()

    # Find the 'primary' progress to show the main bar (usually the first one found)
    active_prog = all_progs[0] if all_progs else None
    
    # Create a map so the UI knows which recipe is running on which host
    prog_map = {p.host_id: p for p in all_progs}

    # 4. GATHER RECIPES & INGREDIENT TOTALS
    recipe_data = []
    source_quantities = {}
    attrib_data = []

    for r in main_item.recipes:
        host_id = find_best_host(r, owner_id, ctx)
        can_do, reason = can_perform_recipe(
            host_id, r, owner_id, ctx)
        recipe_data.append({
            "recipe_id": r.id, 
            "host_id": host_id,
            "can_produce": can_do, 
            "reason": reason
        })

        # Where are the ingredients relative to this worker and location?
        resolved = resolve_recipe_sources(
            host_id, r, ctx)
        for res in resolved:
            s_item = res['item']
            source_quantities[s_item.id] = format_num(res['total_available'])

        # Collect attribute values used in these recipes
        for req_attr in r.attrib_reqs:
            # Check host, owner, and context for this attribute
            for eid in ctx.unique_ids(host_id, owner_id, GENERAL_ID):
                av = AttribVal.query.filter_by(
                    game_token=game_token, subject_id=eid,
                    attrib_id=req_attr.attrib_id).first()
                if av:
                    attrib_data.append({
                        "attrib_id": av.attrib_id, 
                        "subject_id": av.subject_id, 
                        "value": format_num(av.value)
                    })

    # 5. GATHER "USED TO PRODUCE" DATA
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
            "is_ongoing": len(all_progs) > 0,
            "active_recipe_id": active_prog.recipe_id if active_prog else None,
            "active_host_id": active_prog.host_id if active_prog else None,
            "start_time": active_prog.start_time.isoformat() if active_prog else None,
            "rate_duration": active_prog.recipe.rate_duration if active_prog else None
        },
        "sources": [
            {"id": sid, "quantity": sqty}
            for sid, sqty in source_quantities.items()],
        "used_for": used_for_data,
        "attribs": attrib_data,
        "recipes": recipe_data,
        "all_active_hosts": list(prog_map.keys())
    })

@play_bp.route('/production/start/host/<int:host_id>', methods=['POST'])
def start_item_production(host_id):
    game_token = g.game_token
    req = RequestHelper('form')
    recipe_id = req.get_int('recipe_id')
    owner_id = req.get_int('owner_id')

    owner = Entity.query.get((game_token, owner_id))
    ctx = ContextIds(
        owner.id,
        req.get_int('char_id'),
        req.get_int('loc_id'),
    )

    logger.debug(
        f"---- start_item_production() ----"
        f"\nHost:{host_id} | Owner:{owner_id} | Recipe:{recipe_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    success, message = start_production(
        host_id, recipe_id, owner_id, ctx)
    if success:
        return '', HTTPStatus.NO_CONTENT
    # BAD_REQUEST causes res.ok to be false in JS
    return jsonify({"message": message}), HTTPStatus.BAD_REQUEST

@play_bp.route('/production/stop/host/<int:host_id>/item/<int:item_id>', methods=['POST'])
def stop_item_production(host_id, item_id):
    if stop_production(host_id, item_id):
        return '', HTTPStatus.NO_CONTENT
    item = Item.query.get((g.game_token, item_id))
    if not item:
        return jsonify(
            {"message": "Item not found"}), HTTPStatus.NOT_FOUND
    return jsonify(
        {"message": f"{item.name} not in progress."}), HTTPStatus.BAD_REQUEST

@play_bp.route('/production/instant/host/<int:host_id>', methods=['POST'])
def instant_item_production(host_id):
    req = RequestHelper('form')
    owner_id = req.get_int('owner_id')
    recipe_id = req.get_int('recipe_id')
    num_batches = req.get_int('batches')

    ctx = ContextIds(
        owner_id,
        session.get('old_char_id'),
        session.get('old_loc_id'),
        host_id
    )

    recipe = Recipe.query.get((g.game_token, recipe_id))
    if not recipe:
        return jsonify({"message": "Recipe not found."}), HTTPStatus.BAD_REQUEST

    # Perform production
    actual_done, halt_reason = execute_production(
        host_id, recipe, owner_id, ctx, batches=num_batches)
    
    if actual_done > 0:
        db.session.commit()
        msg = f"Obtained {actual_done} batch{'es' if actual_done > 1 else ''}."
        if halt_reason:
            msg += f" Stopped early: {halt_reason}"
        return jsonify({"message": msg}), HTTPStatus.OK
    
    return jsonify({
            "message": halt_reason or "Production failed."
        }), HTTPStatus.BAD_REQUEST

# ------------------------------------------------------------------------
# Events & Dice
# ------------------------------------------------------------------------

@play_bp.route('/play/event/<int:id>', methods=['GET'])
def play_event(id):
    game_token = g.game_token
    req = RequestHelper('args')
    event = Event.query.get_or_404((game_token, id))
    capture_origin(name=event.name)
    
    # Semantic Context
    subject_id = req.get_int('subject_id')
    subject = Entity.query.get((game_token, subject_id)) if subject_id else None

    owner_id = req.get_int('owner_id')
    owner = Entity.query.get((game_token, owner_id)) if owner_id else None

    ctx_char_id = req.get_int('char_id') or session.get('old_char_id')
    ctx_char = Character.query.get((game_token, ctx_char_id)) if ctx_char_id else None

    ctx_loc_id = req.get_int('loc_id') or session.get('old_loc_id')
    if subject and subject.entity_type == Character.TYPENAME:
        ctx_loc_id = subject.location_id
    elif owner and owner.entity_type == Character.TYPENAME:
        ctx_loc_id = owner.location_id
    elif ctx_char:
        ctx_loc_id = ctx_char.location_id
    ctx_loc = Location.query.get((game_token, ctx_loc_id)) if ctx_loc_id else None

    # Get list of all available nearby entities

    other_entities_here = []
    other_piles_here = {}
    subject_pile_qty = None
    if ctx_loc_id:
        other_entities_here = Character.query.options(
            joinedload(Character.attrib_values)) \
            .filter_by(game_token=game_token, location_id=ctx_loc_id) \
            .filter(Character.id != subject_id) \
            .all()

        #TODO: can use quantities of other piles
        #
        #piles_here = Pile.query.filter_by(
        #    game_token=game_token, owner_id=ctx_loc_id
        #).all()
        #subject_piles = [p for p in piles_here if p.item_id == subject_id]
        #for p in piles_here:
        #    if len(subject_piles) == 1 and p.item_id == subject_id:
        #        subject_pile_qty = p.quantity
        #        continue
        #    other_piles_here.setdefault(p.item_id, []).append(p.quantity)

    # Get available children

    #TODO: handle qty fields
    #
    #children_piles = {}
    #if ctx_char:
    #    children_piles.setdefault(ctx_char.id, []).extend(ctx_char.piles)
        
    # Get determinants for each role
    # TODO: can we apply the same logic for effects?

    role_dets = {}
    for d in event.determinants:
        #for efield in (d.infield, d.outfield):
        efield = d.infield
        if efield:
            role_dets.setdefault(efield.role, set()).add(d)

    # Get list of nearby entities that have those attributes
    # to fill the select box for each role.
    # Display as text if only one option,
    # and disallow event if no candidate for role.
    # Include attr or qty value in the list.
    
    def meets_det(det, entity):
        if not entity: return False
        if det.infield and det.infield.field_mode == Participant.ATTR \
                and det.infield.attrib_id:
            if not any(
                    av.attrib_id == d.infield.attrib_id
                    for av in entity.attrib_values):
                return False

        # TODO: check for event distance requirement e.g. 30ft (6 tiles)
        # dist = distance_between(owner.position, c.position)
        # if dist is not None and dist > d.distance_reqired

        # Quantity check
        if det.infield and det.infield.field_mode == Participant.QTY \
                and det.infield.item_id:
            item_def = Item.query.get((game_token, det.infield.item_id))

            if item_def and item_def.storage_type == StorageType.UNIVERSAL:
                return entity.id == GENERAL_ID

            if entity.entity_type == Item.TYPENAME:
                check_owner_id = owner_id
            else:
                check_owner_id = entity.id

            has_pile = Pile.query.filter_by(
                game_token=game_token, 
                owner_id=check_owner_id,
                item_id=det.infield.item_id
            ).first() is not None
            if not has_pile:
                return False

        return True

    eligible_role_entities = {}
    dets_not_met = {}
    for role, detlist in role_dets.items():
        # Check if this role is for the General Owner (Uses Universal Items)
        is_universal_role = False
        for d in detlist:
            if d.infield and d.infield.field_mode == Participant.QTY \
                    and d.infield.item_id:
                item = Item.query.get((game_token, d.infield.item_id))
                if item and item.storage_type == StorageType.UNIVERSAL:
                    is_universal_role = True
                    break
        
        if is_universal_role:
            search_pool= [Entity.query.get((game_token, GENERAL_ID))]
        elif role == Participant.SUBJECT:
            search_pool = [subject] if subject else other_entities_here
        elif role == Participant.OWNER:
            search_pool = [owner] if owner else other_entities_here
        elif role == Participant.AT:
            search_pool = [ctx_loc] if ctx_loc else other_entities_here
        else:
            search_pool = other_entities_here

        role_candidates = None
        for d in detlist:
            meeting_this_det = {ent for ent in search_pool if meets_det(d, ent)}
            if role_candidates is None:
                role_candidates = meeting_this_det
            else:
                role_candidates &= meeting_this_det # Intersection
            if not meeting_this_det:
                dets_not_met.setdefault(role, []).append(d)

        eligible_role_entities[role] = list(role_candidates) if role_candidates else []

    return render_template(
        'play/event.html',
        event=event,
        subject=subject,
        ctx_char=ctx_char,
        ctx_loc=ctx_loc,
        role_entities=eligible_role_entities,
        dets_not_met=dets_not_met,
        Participant=Participant,
        link_letters=LinkLetters(excluded='moer')
    )

@play_bp.route('/event/preview/<int:id>', methods=['POST'])
def event_preview(id):
    """AJAX helper to calculate modifiers based on current UI selections."""
    game_token = g.game_token
    event = Event.query.get((game_token, id))
    req = RequestHelper('form')
    
    role_entities = {}
    for key in req:
        if key.endswith(Participant.FORM_SUFFIX):
            role_name = Participant.formkey_to_role(key)
            role_entities[role_name] = req.get_int(key)
    
    modifiers = calculate_determinants(event, role_entities)
    return jsonify(modifiers)

@play_bp.route('/event/roll/<int:id>', methods=['POST'])
def roll_event(id):
    game_token = g.game_token
    event = Event.query.get_or_404((game_token, id))
    req = RequestHelper('form')

    role_entities = {}
    for key in req:
        if key.endswith(Participant.FORM_SUFFIX):
            role_name = Participant.formkey_to_role(key)
            role_entities[role_name] = req.get_int(key)

    if event.outcome_type == OutcomeType.ROLLER:
        n_dice = req.get_int('num_dice', 1)
        sides = req.get_int('sides', 20)
        bonus = req.get_int('bonus', 0)
        result_num, result_str = roll_for_system_outcome(
            id, n_dice, sides, bonus)
    else:
        difficulty = req.get_float('difficulty', 0.55)
        result_num, result_str = roll_for_outcome(
            id, role_entities, difficulty)
    
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
        return jsonify(
            {"message": "Missing target info"}), HTTPStatus.BAD_REQUEST

    from .logic_event import apply_event_change
    apply_event_change(key_id, key_type, container_id, new_value)
    
    # Log
    container = Entity.query.get((g.game_token, container_id))
    key_def = Entity.query.get((g.game_token, key_id))
    add_message(f"Updated {key_def.name} on {container.name} to {new_value}")

    return '', HTTPStatus.NO_CONTENT

@play_bp.route('/play/attrib/<int:attrib_id>/subject/<int:subject_id>', methods=['GET', 'POST'])
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
        
        # Log
        add_message(f"Modified {attribute.name} on {subject.name} to {new_val}")
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
