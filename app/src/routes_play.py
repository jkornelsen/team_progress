import logging
import json
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify,
    g, session, current_app)
from http import HTTPStatus
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import joinedload
from app.models import (
    db, Entity, Item, Character, Location, Attrib, Event,
    Pile, AttribVal, Recipe, RecipeAttribReq,
    Operation, OutcomeType, SuccessTier, EventFactor, PartyTarget,
    DestExit, LocDest, EventLink, EntityAbility, EventField,
    Progress, Scenario, WinRequirement, GameMessage,
    AutobattleStage,
    GENERAL_ID, StorageType, Participant)
from app.utils import (
    RequestHelper, ContextIds, format_num, parse_coords, LinkLetters,
    capture_origin, redirect_back, name_stripped, sort_by_name_stripped,
    maskable_name)
from .logic_piles import transfer_item
from .logic_event import (
    roll_for_outcome, roll_for_system_outcome, check_outcome_success,
    calculate_determinants, resolve_anchor_id, get_chain_results,
    preview_effects, resolve_effects, get_entity_value, is_factor_met,
    do_effect_change, process_all_auto_effects, format_for_display,
    apply_operation)
from .logic_progress import (
    tick_all_active, start_production, stop_production)
from .logic_production import (
    find_best_host, resolve_recipe_sources, can_perform_recipe,
    execute_production)
from .logic_navigation import (
    move_group, get_cohesive_party, get_available_destinations,
    arrive_at_destination,
    is_in_grid, blocked_by_local_item, find_nearest_available_pos, is_adjacent,
    get_party_set, is_in_same_party, assign_parties_and_sort)
from .logic_objectives import validate_requirements
from .logic_autobattle import (
    run_battle_round, run_battle_reset, get_battle_participants, get_char_stat)
from .logic_user_interaction import add_message, get_chronicle
from .presenters import ItemPlayPresenter

logger = logging.getLogger(__name__)
play_bp = Blueprint('play', __name__)

# ------------------------------------------------------------------------
# The Overview (Dashboard)
# ------------------------------------------------------------------------

@play_bp.route('/overview')
def overview():
    game_token = g.game_token
    
    # Fetch Top-Level Entities
    chars = Character.query.filter_by(
        game_token=game_token, toplevel=True).order_by(name_stripped()).all()
    locs = Location.query.filter_by(
        game_token=game_token, toplevel=True).order_by(name_stripped()).all()
    items = Item.query.filter_by(
        game_token=game_token, toplevel=True).order_by(name_stripped()).all()
    events = Event.query.filter_by(
        game_token=game_token, toplevel=True).order_by(name_stripped()).all()
    
    # Items currently being produced
    tick_all_active()
    items_in_production = {
        p.product_id for p in Progress.query.filter_by(
            game_token=game_token
        ).all()
    }

    # Check Win Requirements
    scenario = db.session.get(Scenario, game_token)
    enriched_win_reqs, all_met = validate_requirements(scenario)
    
    # Recent Messages
    messages = get_chronicle()

    return render_template(
        'play/overview.html',
        characters=chars,
        locations=locs,
        items=items,
        items_in_production=items_in_production,
        events=events,
        scenario=scenario,
        win_reqs=enriched_win_reqs,
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
    location = db.get_or_404(Location, (game_token, id))
    capture_origin(name=location.name)
    session['old_loc_id'] = id
    logger.debug(f"old_loc_id={id}")
    
    # 1. Fetch Characters & Items
    characters_here = Character.query.filter_by(
        game_token=game_token, location_id=id
    ).order_by(name_stripped()).all()
    characters_here = assign_parties_and_sort(characters_here)
    
    inventory_piles = sort_by_name_stripped(
        Pile.query.filter_by(game_token=game_token, owner_id=id).all(),
        lambda p: p.item)

    # Validate the session's char_id
    current_char_id = session.get('old_char_id')
    current_char = next(
        (c for c in characters_here if c.id == current_char_id), None)
    if not current_char:
        session.pop('old_char_id', None)

    # 2. Fix Incorrectly Positioned Entities
    if location.has_grid:
        needs_commit = False

        # Validate Characters (No overlapping allowed)
        occupied_in_fix = set()
        for char in characters_here:
            out_of_bounds = not is_in_grid(location, char.position)
            # If out of bounds OR someone already took this spot during the fix
            if not char.position or out_of_bounds \
                    or tuple(char.position) in occupied_in_fix:
                start_search = char.position or [1, 1]
                # Find nearest that isn't blocked by items/zones, 
                # and isn't in our 'occupied_in_fix' set
                new_pos = find_nearest_available_pos(
                    location, start_search, exclude_char_id=char.id)
                
                # Check for collisions with characters we haven't processed yet
                while new_pos and tuple(new_pos) in occupied_in_fix:
                     # Bump search if there's a collision in the local tracker
                     new_pos = find_nearest_available_pos(
                        location,
                        [new_pos[0]+1, new_pos[1]],
                        exclude_char_id=char.id)

                if new_pos:
                    char.position = new_pos
                    needs_commit = True
            
            if char.position:
                occupied_in_fix.add(tuple(char.position))

        # Validate & Disperse Items
        for pile in inventory_piles:
            is_local = pile.item.storage_type == StorageType.LOCAL
            out_of_bounds = not is_in_grid(
                location, pile.position, check_zones=False)
            blocked_by_zone = not is_local and not out_of_bounds and \
                not is_in_grid(location, pile.position, check_zones=True)
            overlap_collision = not is_local and blocked_by_local_item(
                id, pile.position)

            if not pile.position or out_of_bounds or blocked_by_zone \
                    or overlap_collision:
                # Find the nearest open floor space
                new_pos = find_nearest_available_pos(
                    location, pile.position or [1, 1])
                pile.position = new_pos
                needs_commit = True

        if needs_commit:
            db.session.commit()
            # Refresh list because some piles may have been merged/deleted
            inventory_piles = Pile.query.filter_by(
                game_token=game_token, owner_id=id
            ).all()

    # 3. Fetch Exits In Grid
    stmt = (
        select(LocDest)
        .where(
            LocDest.game_token == game_token,
            or_(
                and_(
                    LocDest.loc1_id == id, 
                    LocDest.direction.in_([DestExit.BOTH, DestExit.LOC1])
                ),
                and_(
                    LocDest.loc2_id == id, 
                    LocDest.direction.in_([DestExit.BOTH, DestExit.LOC2])
                )
            )
        )
    )
    destinations = db.session.scalars(stmt).all()
    destinations.sort(key=lambda r: r.other_loc(id).name.lower())

    grid_exits = []
    for dest in destinations:
        door = dest.door_at(id)
        if door and len(door) == 2:
            if is_in_grid(location, door, check_zones=False):
                target = dest.other_loc(id)
                grid_exits.append({
                    'x': door[0],
                    'y': door[1],
                    'name': maskable_name(target),
                    'target_id': target.id,
                    'masked': target.masked
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
    referenced_data = sort_by_name_stripped(
        referenced_data, lambda d: d['item'])

    # 6. Grid Driver (The moving character)
    url_driver = request.args.get('active_char_id', type=int)
    session_driver = session.get('grid_driver_id')
    session_context = session.get('old_char_id')
    active_char_id = None
    ids_to_check = [url_driver, session_driver, session_context]
    room_char_ids = {c.id for c in characters_here}
    for cand_id in ids_to_check:
        if cand_id in room_char_ids:
            active_char_id = cand_id
            break
    if not active_char_id and characters_here:
        active_char_id = characters_here[0].id
    session['grid_driver_id'] = active_char_id

    return render_template(
        'play/location.html',
        location=location,
        inventory_piles=inventory_piles,
        characters_here=characters_here,
        destinations=destinations,
        grid_exits=grid_exits,
        referenced_items=referenced_data,
        attrib_values=sort_by_name_stripped(
            AttribVal.query.filter_by(
                game_token=game_token, subject_id=id).all(),
            lambda p: p.attrib),
        ctx_char=current_char,
        active_char_id=active_char_id,
        travel_with_party=session.get('travel_with_party', False),
        link_letters=LinkLetters(excluded='ctmoedwu')
    )

@play_bp.route('/play/char/<int:id>')
def play_character(id):
    game_token = g.game_token
    character = db.get_or_404(Character, (game_token, id))
    capture_origin(name=character.name)
    req = RequestHelper('args')
    exit_loc_id = req.get_int('auto_select_exit')
    move_party = req.get_bool('move_party', None)
    if move_party is not None:
        session['travel_with_party'] = move_party
    else:
        move_party = session['travel_with_party']
    session['old_char_id'] = id
    session['grid_driver_id'] = id
    session.pop('old_loc_id', None)
    
    # Identify other party members at this location
    party_members = []
    party_criteria = []
    if character.party:
        party_criteria.append(Character.party == character.party)
        party_criteria.append(Character.name == character.party)
    party_criteria.append(Character.party == character.name)

    if party_criteria:
        all_candidates = Character.query.filter(
            Character.game_token == game_token,
            Character.location_id == character.location_id,
            or_(*party_criteria),
            Character.id != character.id
        ).all()
        if character.location and character.location.has_grid:
            party_members = [
                c for c in get_cohesive_party(character, True)
                if c.id != character.id]
        else:
            party_members = all_candidates

    # Fetch Navigation (Nearby Destinations)
    destinations, has_nonadjacent = get_available_destinations(character)
    
    return render_template(
        'play/character.html',
        character=character,
        inventory=sort_by_name_stripped(
            Pile.query.filter_by(game_token=game_token, owner_id=id).all(),
            lambda p: p.item),
        attrib_values=sort_by_name_stripped(
            AttribVal.query.filter_by(
                game_token=game_token, subject_id=id).all(),
            lambda p: p.attrib),
        destinations=destinations,
        exit_loc_id=exit_loc_id,
        has_nonadjacent=has_nonadjacent,
        party_members=party_members,
        travel_with_party=move_party,
        link_letters=LinkLetters(excluded='gltmoew')
    )

@play_bp.route('/char/<int:id>/drop', methods=['POST'])
def drop_item(id):
    req = RequestHelper('form')
    item_id = req.get_int('item_id')
    qty = req.get_float('quantity')
    
    # Get character to find current location
    char = db.session.get(Character, (g.game_token, id))
    
    # Transfer from Char to Location at current Char position
    success, msg = transfer_item(
        item_id, from_owner_id=id, to_owner_id=char.location_id,
        quantity=qty, to_pos=char.position)
    
    item = db.session.get(Item, (g.game_token, item_id))
    if success:
        db.session.commit()
        add_message(f"{char.name} dropped {qty:g} {item.name}")
        return jsonify({"message": msg}), HTTPStatus.OK
    return jsonify(
        {"message": f"Could not drop {item.name}."}
    ), HTTPStatus.BAD_REQUEST

@play_bp.route('/char/<int:id>/pickup', methods=['POST'])
def pickup_item(id):
    req = RequestHelper('form')
    item_id = req.get_int('item_id')
    qty = req.get_float('quantity')
    pos = parse_coords(req.get_str('pos'))
    slot_id = req.get_str('slot_id')
    game_token = g.game_token
    
    char = db.session.get(Character, (g.game_token, id))
    loc = char.location
    
    # Position Dependency Check
    if loc.dimensions and loc.dimensions[0] > 0:
        if not is_adjacent(char.position, pos):
            return jsonify({
                "message": "You are too far away to pick that up."
            }), HTTPStatus.BAD_REQUEST

    # Transfer from Location to Char
    success, msg = transfer_item(
        item_id, from_owner_id=char.location_id, to_owner_id=id,
        quantity=qty, from_pos=pos
    )
    
    item = db.session.get(Item, (g.game_token, item_id))
    if success:
        if slot_id:
            pile = Pile.query.filter_by(
                game_token=game_token, 
                owner_id=id, 
                item_id=item_id
            ).first()
            if pile:
                pile.slot_id = slot_id

        db.session.commit()
        add_message(f"{char.name} picked up {qty:g} {item.name}")
        return jsonify({"message": msg}), HTTPStatus.OK

    return jsonify(
        {"message": "{char.name} could not pick up {item.name}."}
    ), HTTPStatus.BAD_REQUEST

@play_bp.route('/char/<int:id>/give', methods=['POST'])
def give_item(id):
    req = RequestHelper('form')
    item_id = req.get_int('item_id')
    target_char_id = req.get_int('target_char_id')
    qty = req.get_float('quantity')
    
    char = db.session.get(Character, (g.game_token, id))
    target_char = db.session.get(Character, (g.game_token, target_char_id))
    loc = char.location
    
    # Position Dependency Check
    if loc.dimensions and loc.dimensions[0] > 0:
        if not is_adjacent(char.position, target_char.position):
            return jsonify({
                "message": f"Must be next to {target_char.name} to give items."
            }), HTTPStatus.BAD_REQUEST

    # Transfer from Char to Target Char
    success, msg = transfer_item(
        item_id, from_owner_id=id, to_owner_id=target_char_id,
        quantity=qty
    )

    item = db.session.get(Item, (g.game_token, item_id))
    if success:
        db.session.commit()
        add_message(f"{char.name} gave {qty:g} {item.name} to {target_char.name}")
        return jsonify({"message": msg}), HTTPStatus.OK
    return jsonify(
        {"message": "Could not transfer {item.name} to {target_char.name}."}
    ), HTTPStatus.BAD_REQUEST

@play_bp.route('/char/<int:id>/equip', methods=['POST'])
def equip_item(id):
    """Assigns an item pile to a specific equipment slot."""
    req = RequestHelper('form')
    game_token = g.game_token
    item_id = req.get_int('item_id')
    slot_id = req.get_str('slot_id')
    
    # Store the most recently used slot in the session for UI convenience
    session['default_slot_id'] = slot_id
    
    # Fetch character and item to ensure they exist (for the log message)
    char = db.session.get(Character, (game_token, id))
    item = db.session.get(Item, (game_token, item_id))
    
    if not char or not item:
        return jsonify(
            {'message': 'Character or Item not found.'}), HTTPStatus.NOT_FOUND

    # Find the specific pile in the character's inventory
    pile = Pile.query.filter_by(
        game_token=game_token, 
        owner_id=id, 
        item_id=item_id
    ).first()
    if not pile:
        return jsonify({
            'message': f"No {item.name} found in {char.name}'s inventory."
        }), HTTPStatus.BAD_REQUEST

    # Find if any other item is currently in the target slot for this character
    existing_occupant = Pile.query.filter_by(
        game_token=game_token,
        owner_id=id,
        slot_id=slot_id
    ).first()
    if existing_occupant and existing_occupant.id != pile.id:
        existing_occupant.slot_id = None

    # Update the slot
    pile.slot_id = slot_id
    db.session.commit()

    # Log
    add_message(f"{char.name} equipped {item.name} to {pile.slot_label}")
    return '', HTTPStatus.NO_CONTENT

@play_bp.route('/char/<int:id>/unequip', methods=['POST'])
def unequip_item(id):
    """Removes an item from its equipment slot, returning it to the general pack."""
    req = RequestHelper('form')
    game_token = g.game_token
    item_id = req.get_int('item_id')
    
    char = db.session.get(Character, (game_token, id))
    item = db.session.get(Item, (game_token, item_id))

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
    pile.slot_id = None
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
    session['travel_with_party'] = move_party
    
    success, results = move_group(id, dx, dy, move_party)
    if success:
        db.session.commit()
        return jsonify({"positions": results}), HTTPStatus.OK
    return jsonify({"message": results}), HTTPStatus.BAD_REQUEST

@play_bp.route('/char/<int:id>/go', methods=['POST'])
def char_travel(id):
    req = RequestHelper('form')
    dest_loc_id = req.get_int('dest_id')
    move_party = req.get_bool('move_party')
    session['travel_with_party'] = move_party
    success, message = arrive_at_destination(id, dest_loc_id, move_party)
    if success:
        db.session.commit()
        return '', HTTPStatus.NO_CONTENT
    return jsonify({"message": message}), HTTPStatus.BAD_REQUEST

@play_bp.route('/play/sync_session', methods=['POST'])
def sync_session():
    req = RequestHelper('form')
    if 'grid_driver_id' in req:
        session['grid_driver_id'] = req.get_int('grid_driver_id')
    if 'travel_with_party' in req:
        session['travel_with_party'] = req.get_bool('travel_with_party')
    return '', 204

# ------------------------------------------------------------------------
# Item Route
# ------------------------------------------------------------------------

@play_bp.route('/play/item/<int:id>')
def play_item(id):
    presenter = ItemPlayPresenter(id, RequestHelper('args'))
    return render_template(
        'play/item.html',
        **presenter.get_template_context())

# ------------------------------------------------------------------------
# Production Routes
# ------------------------------------------------------------------------

@play_bp.route(
    '/production/status/item/<int:item_id>/owner/<int:owner_id>',
    methods=['POST'])
def item_production_status(item_id, owner_id):
    """
    Heartbeat endpoint to calculate current progress and
    refresh recipe availability.
    """
    session.modified = False # prevent stale cookie overwrites
    game_token = g.game_token
    req = RequestHelper('form')
    pos = req.get_coords('pos')
    
    # Contextual IDs
    char_id = req.get_int('char_id')
    loc_id = req.get_int('loc_id')
    ctx = ContextIds(owner_id, char_id, loc_id)

    logger.debug(
        f"---- item_production_status() ----\n"
        f"Item:{item_id} | Owner:{owner_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    # 1. Tick the world
    tick_all_active()

    # 2. Gather data for the specific pile we are viewing
    main_item = db.session.get(Item, (game_token, item_id))
    if not main_item:
        return jsonify({"message": "Item not found"}), HTTPStatus.NOT_FOUND

    pile_query = Pile.query.filter_by(
        game_token=game_token, owner_id=owner_id, item_id=item_id)
    if pos:
        pile_query = pile_query.filter_by(position=list(pos))
    main_pile = pile_query.first()
    
    # 3. Gather progress for all possible hosts
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

    # 4. Gather recipes & ingredient totals
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

    # 5. Gather "used to produce" data
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
            "rate_duration": active_prog.recipe.rate_duration if active_prog else None,
            "stop_at": active_prog.stop_at if active_prog else None
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
    stop_at = req.get_float('stop_at', default=None)

    owner = db.session.get(Entity, (game_token, owner_id))
    ctx = ContextIds(
        owner_id,
        req.get_int('char_id'),
        req.get_int('loc_id'),
        position=req.get_coords('pos')
    )

    logger.debug(
        f"---- start_item_production() ----"
        f"\nHost:{host_id} | Owner:{owner_id} | Recipe:{recipe_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    success, message = start_production(
        host_id, recipe_id, owner_id, ctx, stop_at=stop_at)
    if success:
        return '', HTTPStatus.NO_CONTENT
    # BAD_REQUEST causes res.ok to be false in JS
    return jsonify({"message": message}), HTTPStatus.BAD_REQUEST

@play_bp.route('/production/stop/host/<int:host_id>/item/<int:item_id>', methods=['POST'])
def stop_item_production(host_id, item_id):
    if stop_production(host_id, item_id):
        return '', HTTPStatus.NO_CONTENT
    item = db.session.get(Item, (g.game_token, item_id))
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
        host_id,
        position=req.get_coords('pos')
    )

    recipe = db.session.get(Recipe, (g.game_token, recipe_id))
    if not recipe:
        return jsonify({"message": "Recipe not found."}), HTTPStatus.BAD_REQUEST

    # Perform production
    actual_done, halt_reason = execute_production(
        host_id, recipe, owner_id, ctx, batches=num_batches)
    
    if actual_done > 0:
        db.session.commit()
        if halt_reason:
            msg = f"Obtained {actual_done}" \
                  f" batch{'es' if actual_done > 1 else ''}." \
                  f" Stopped early: {halt_reason}"
            return jsonify({"message": msg}), HTTPStatus.OK
        return '', HTTPStatus.NO_CONTENT
    
    return jsonify({
            "message": halt_reason or "Production failed."
        }), HTTPStatus.BAD_REQUEST

# ------------------------------------------------------------------------
# Attributes
# ------------------------------------------------------------------------

@play_bp.route('/play/attrib/<int:attrib_id>/subject/<int:subject_id>', methods=['GET', 'POST'])
def play_attrib(attrib_id, subject_id):
    game_token = g.game_token
    attribute = db.get_or_404(Attrib, (game_token, attrib_id))
    subject = db.get_or_404(Entity, (game_token, subject_id))
    capture_origin(name=f"{subject.name} {attribute.name}")
    
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
        
        operand = req.get_float('operand') if op != Operation.ASSIGN else None
        value_for_assign = req.get_float('value') or req.get_float('operand')

        if op == Operation.ASSIGN:
            new_val = apply_operation(
                val_record.value, value_for_assign, op, attrib=attribute)
        else:
            new_val = apply_operation(
                val_record.value, operand, op, attrib=attribute)
        val_record.value = new_val
        db.session.commit()

        # Log
        op_wording = {
            Operation.ADD:    {"verb": "Increased",  "prep": "by"},
            Operation.SUB:    {"verb": "Reduced",    "prep": "by"},
            Operation.MULT:   {"verb": "Multiplied", "prep": "by"},
            Operation.DIV:    {"verb": "Divided",    "prep": "by"},
            Operation.ASSIGN: {"verb": "Set",        "prep": "to"},
        }
        op_words = op_wording.get(op, {"verb": "Modified", "prep": "to"})
        if op == Operation.ASSIGN:
            val_str = attribute.format_value(new_val)
        else:
            val_str = f"{round(abs(operand), 2):g} = " \
                      f"{attribute.format_value(new_val)}"
        add_message(
            f"{op_words['verb']} {subject.name} {attribute.name}"
            f" {op_words['prep']} {val_str}"
        )
        return redirect(request.url)

    # Get reverse dependencies (items needing this for recipes)
    items_requiring_this = Item.query.join(Recipe).join(RecipeAttribReq).filter(
        RecipeAttribReq.attrib_id == attrib_id,
        Item.game_token == game_token
    ).all()
    items_requiring_this = sort_by_name_stripped(items_requiring_this)

    # Get events using this attribute
    events_raw = Event.query.join(EventFactor).join(
        EventField, or_(
            EventFactor.infield_id == EventField.id,
            EventFactor.outfield_id == EventField.id
        )
    ).filter(
        EventField.attrib_id == attrib_id,
        Event.game_token == game_token
    ).distinct().all()
    events_using_this = sort_by_name_stripped(events_raw)

    return render_template(
        'play/attrib.html', 
        attribute=attribute, 
        subject=subject, 
        attrib_value=val_record,
        items_requiring_this=items_requiring_this,
        events_using_this=events_using_this,
        link_letters=LinkLetters(excluded='moesct'))

# ------------------------------------------------------------------------
# Events
# ------------------------------------------------------------------------

@play_bp.route('/play/event/<int:id>', methods=['GET'])
def play_event(id):
    game_token = g.game_token
    req = RequestHelper('args')
    event = db.get_or_404(Event, (game_token, id))
    capture_origin(name=event.name)
    
    # Semantic Context
    sticky_role_entities = session.get('role_entities', {})
    for key in req:
        if key.endswith(Participant.ROLE_SUFFIX):
            role_name = Participant.formkey_to_role(key)
            val = req.get_int(key)
            if val:
                sticky_role_entities[role_name] = val
    session['role_entities'] = sticky_role_entities

    subject_id = req.get_int('subject_role_id') \
        or req.get_int('subject_id') \
        or sticky_role_entities.get(Participant.SUBJECT)
    subject = db.session.get(
        Entity, (game_token, subject_id)) if subject_id else None

    owner_id = req.get_int('owner_role_id') or req.get_int('owner_id')
    owner = db.session.get(
        Entity, (game_token, owner_id)) if owner_id else None

    ctx_char_id = req.get_int('target char_role_id') \
        or req.get_int('char_id') \
        or session.get('old_char_id') \
        or sticky_role_entities.get(Participant.TARGET)
    ctx_char = db.session.get(
        Character, (game_token, ctx_char_id)) if ctx_char_id else None

    ctx_loc_id = req.get_int('at_role_id') \
        or req.get_int('loc_id') \
        or session.get('old_loc_id') \
        or sticky_role_entities.get(Participant.AT)
    if subject and subject.entity_type == Character.TYPENAME:
        ctx_loc_id = subject.location_id
    elif subject and subject.entity_type == Location.TYPENAME:
        ctx_loc_id = subject.id
    elif owner and owner.entity_type == Character.TYPENAME:
        ctx_loc_id = owner.location_id
    elif owner and owner.entity_type == Location.TYPENAME:
        ctx_loc_id = owner.id
    elif ctx_char:
        ctx_loc_id = ctx_char.location_id
    ctx_loc = db.session.get(
        Location, (game_token, ctx_loc_id)) if ctx_loc_id else None
    logger.debug(f"ctx_loc_id={ctx_loc_id}")

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
        
    # Identify all roles that need resolving
    roles_to_resolve = {
        fld.role for f in event.factors 
        for fld in [f.infield, f.outfield] if fld and fld.role
    }
    if event.outcome_type == OutcomeType.COORDS:
        roles_to_resolve.add(Participant.AT)

    # Get list of available entities available for each role
    eligible_role_entities = {}
    fields_not_met = {}
    for role in roles_to_resolve:
        if role == Participant.PRESELECTED:
            continue
        if role == Participant.SELECTED:
            search_pool = Entity.query.filter(
                Entity.game_token == game_token,
                Entity.id != GENERAL_ID,
                Entity.entity_type.in_(['character', 'location', 'item'])
            ).all()
        elif role == Participant.SUBJECT:
            search_pool = [subject] if subject else other_entities_here
        elif role == Participant.AT:
            search_pool = [ctx_loc] if ctx_loc else other_entities_here
        else:
            search_pool = other_entities_here
        logger.debug(
            f"routes_play: search_pool for {role} = "
            f"{[e.name for e in search_pool]}")
        role_candidates = set(search_pool)

        # Filter By Party Targeting
        if role == Participant.TARGET and event.party_targeting \
                and event.party_targeting != PartyTarget.ANY:
            targeting = event.party_targeting
            party_name = (event.party_name or "").lower()
            filtered_candidates = set()
            for ent in role_candidates:
                if ent.entity_type != Character.TYPENAME:
                    filtered_candidates.add(ent)
                    continue
                party_set = get_party_set(ent)
                is_same = is_in_same_party(subject, ent) if subject else False
                if targeting == PartyTarget.SAME and not is_same:
                    continue
                if targeting == PartyTarget.NOT_SAME and is_same:
                    continue
                if targeting == PartyTarget.TARGET_NAME \
                        and party_name not in party_set:
                    continue
                if targeting == PartyTarget.EXCLUDE_NAME \
                        and party_name in party_set:
                    continue
                filtered_candidates.add(ent)
            if not filtered_candidates:
                if role not in fields_not_met:
                    fields_not_met[role] = {'positive': [], 'negated': []}
                party_labels = {
                    PartyTarget.SAME: "Same Party",
                    PartyTarget.NOT_SAME: "Same Party",
                    PartyTarget.TARGET_NAME: f"Party '{event.party_name}'",
                    PartyTarget.EXCLUDE_NAME: f"Party '{event.party_name}'"
                }
                logic_key = 'negated' if targeting in [
                    PartyTarget.NOT_SAME, PartyTarget.EXCLUDE_NAME
                    ] else 'positive'
                fields_not_met[role][logic_key].append(
                    (None, party_labels[targeting]))
                logger.info(
                    f"party restriction not met for {role}:"
                    f" {targeting} {party_name}")
            role_candidates = filtered_candidates

        # Filter By Factor Comparisons
        factors = [
            f for f in event.factors
            if f.infield and f.infield.role == role]
        cand_failures = {}
        for ent in role_candidates:
            fails = [
                f for f in factors
                if not is_factor_met(
                    f, ent, subject_id=subject_id,
                    require_comparison=(f.usage_type == Participant.DET)
                )
            ]
            cand_failures[ent] = fails

        eligible = {ent for ent, fails in cand_failures.items() if not fails}
        if eligible:
            role_candidates = eligible
        elif cand_failures:
            # Find minimum failures among candidates (closest matches)
            min_fails = min(len(fails) for fails in cand_failures.values())
            
            # Collect unique failed factors from closest candidates in original factor order
            closest_failed_factors = [
                f for f in factors
                if any(f in cand_failures[ent]
                    for ent in cand_failures
                    if len(cand_failures[ent]) == min_fails)
            ]
            if role not in fields_not_met:
                fields_not_met[role] = {'positive': [], 'negated': []}
            for factor in closest_failed_factors:
                logic_key = 'negated' if factor.negate else 'positive'
                fields_not_met[role][logic_key].append((factor, factor.infield))
                logger.info(
                    f"factor {factor.id} not met: "
                    f"{role}, {logic_key}, {factor.infield.get_field_name()}")
            role_candidates = set()

        eligible_role_entities[role] = sort_by_name_stripped(
            list(role_candidates))

    # Entities that call or are involved with this event
    all_related = {}

    # Parent events that call this event
    for e in (
        db.session.query(Event)
        .join(EventLink, (Event.id == EventLink.parent_id) &
                         (Event.game_token == EventLink.game_token))
        .filter(EventLink.child_id == id)
        .filter(EventLink.game_token == game_token)
        .all()
    ):
        all_related[e.id] = e

    # Entities that call this event via abilities
    for e in (
        db.session.query(Entity)
        .join(EntityAbility, (Entity.id == EntityAbility.entity_id) &
                             (Entity.game_token == EntityAbility.game_token))
        .filter(EntityAbility.event_id == id)
        .filter(EntityAbility.game_token == game_token)
        .all()
    ):
        all_related[e.id] = e

    # Blueprint entities involved via factors
    for f in event.factors:
        for field in [f.infield, f.outfield]:
            if not field: continue

            def add_ent(ent):
                if ent:
                    all_related[ent.id] = ent

            if field.attrib_id:
                add_ent(db.session.get(Attrib, (game_token, field.attrib_id)))
            if field.item_id:
                add_ent(Item.query.filter_by(
                    game_token=game_token,
                    id=field.item_id,
                    masked=False).first())
            if field.recipe_id:
                rec = db.session.get(Recipe, (game_token, field.recipe_id))
                if rec:
                    add_ent(Item.query.filter_by(
                        game_token=game_token,
                        id=rec.product_id,
                        masked=False).first()
                    )
            if field.char_id:
                add_ent(db.session.get(Character, (game_token, field.char_id)))
            if field.loc_id:
                add_ent(db.session.get(Location, (game_token, field.loc_id)))

    # Chained events
    for link in event.chained:
        ent = link.child
        all_related[ent.id] = ent

    # Attribute selection
    if event.selection_attrib_id:
        attrib = db.session.get(
            Attrib, (game_token, event.selection_attrib_id))
        all_related[attrib.id] = attrib

    related = sort_by_name_stripped(list(all_related.values()))

    return render_template(
        'play/event.html',
        event=event,
        subject=subject,
        ctx_char=ctx_char,
        ctx_loc=ctx_loc,
        role_entities=eligible_role_entities,
        sticky_roles=sticky_role_entities,
        fields_not_met=fields_not_met,
        related_entities=related,
        all_chars=Character.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_locs=Location.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        OutcomeType=OutcomeType,
        SuccessTier=SuccessTier,
        Participant=Participant,
        Operation=Operation,
        link_letters=LinkLetters(excluded='moeraijk')
    )

@play_bp.route('/event/preview/<int:id>', methods=['POST'])
def event_preview(id):
    """AJAX helper to calculate modifiers and effect targets based
    on UI selections.
    """
    game_token = g.game_token
    event = db.session.get(Event, (game_token, id))
    req = RequestHelper('form')
    roll_val = req.get_json('roll_value')
    
    role_entities = {}
    for key in req:
        if key.endswith(Participant.ROLE_SUFFIX):
            role_name = Participant.formkey_to_role(key)
            role_entities[role_name] = req.get_int(key)
    
    modifiers = calculate_determinants(event, role_entities)
    effect_previews = preview_effects(event, role_entities, roll_val)
    return jsonify({
        "modifiers": modifiers,
        "effect_previews": effect_previews
    })

@play_bp.route('/event/roll/<int:id>', methods=['POST'])
def roll_event(id):
    game_token = g.game_token
    event = db.get_or_404(Event, (game_token, id))
    req = RequestHelper('form')

    role_entities = {}
    for key in req:
        if key.endswith(Participant.ROLE_SUFFIX):
            role_name = Participant.formkey_to_role(key)
            role_entities[role_name] = req.get_int(key)

    if event.outcome_type == OutcomeType.ROLLER:
        n_dice = req.get_int('num_dice', 1)
        sides = req.get_int('sides', 20)
        bonus = req.get_int('bonus', 0)
        result_val, result_str, tier = roll_for_system_outcome(
            id, n_dice, sides, bonus)
    else:
        difficulty = req.get_float('difficulty', 0.55)
        result_val, result_str, tier = roll_for_outcome(
            id, role_entities, difficulty)

    resolved_effects, ledger = resolve_effects(
        event, role_entities, result_val, tier)
    process_all_auto_effects(event, role_entities, result_val, tier)
    db.session.commit()
    
    chain_results = get_chain_results(
        event, role_entities, result_val, tier, ledger)

    return jsonify({
        "result_value": result_val,
        "result_val_display": format_for_display(result_val),
        "full_display": result_str,
        "tier": tier,
        "chain_options": chain_results,
        "resolved_effects": resolved_effects
    })

@play_bp.route('/event/apply-effect/<int:factor_id>', methods=['POST'])
def apply_single_effect(factor_id):
    req = RequestHelper('form')
    eff = db.get_or_404(EventFactor, factor_id)
    
    role_entities = {
        Participant.formkey_to_role(k): req.get_int(k)
        for k in req if k.endswith(Participant.ROLE_SUFFIX)
    }
    roll_val = req.get_json('roll_value')
    success, message = do_effect_change(eff, roll_val, role_entities)
    db.session.commit()
    
    if not success:
        return jsonify({"message": message}), HTTPStatus.BAD_REQUEST

    return '', HTTPStatus.NO_CONTENT

# ------------------------------------------------------------------------
# Auto Battle
# ------------------------------------------------------------------------

@play_bp.route('/play/autobattle/<int:loc_id>')
def play_autobattle(loc_id):
    game_token = g.game_token
    location = db.get_or_404(Location, (game_token, loc_id))
    capture_origin(name=location.name)
    session['old_loc_id'] = loc_id
    
    parties = get_battle_participants(loc_id)
    
    # Enrich the character objects with HP for the template
    for p_name in parties:
        for c in parties[p_name]:
            c.hp = get_char_stat(c, 'hp')
            c.max_hp = get_char_stat(c, 'max_hp')

    return render_template(
        'play/autobattle.html',
        location=location,
        parties=parties,
        messages=get_chronicle(12),
        link_letters=LinkLetters(excluded='snmoe'),
        AutobattleStage=AutobattleStage
    )

@play_bp.route('/play/autobattle/<int:loc_id>/step', methods=['POST'])
def autobattle_step(loc_id):
    game_token = g.game_token
    # 1. Run the round logic
    success, msg = run_battle_round(loc_id)
    
    # 2. Fetch the latest state
    parties = get_battle_participants(loc_id)
    char_stats = {}
    active_parties = []
    
    for p_name, members in parties.items():
        alive_in_party = 0
        for c in members:
            hp = get_char_stat(c, 'hp')
            max_hp = get_char_stat(c, 'max_hp')
            char_stats[c.id] = {
                "hp": format_num(hp),
                "max_hp": format_num(max_hp),
                "is_dead": hp <= 0
            }
            if hp >= 1:
                alive_in_party += 1
        if alive_in_party > 0:
            active_parties.append(p_name)

    battle_continues = len(active_parties) > 1
    if not battle_continues:
        if len(active_parties) == 1:
            add_message(f"-- {active_parties[0]} Wins Round --")
        else:
            add_message("-- No One Wins The Round! --")

    # 3. Fetch recent messages formatted for the log
    messages = [{
        "time": m.timestamp.strftime('%H:%M'),
        "text": m.message,
        "count": m.count
    } for m in get_chronicle(12)]

    return jsonify({
        "success": success,
        "char_stats": char_stats,
        "battle_continues": battle_continues,
        "log": messages
    })

@play_bp.route('/play/autobattle/<int:loc_id>/reset', methods=['POST'])
def autobattle_reset(loc_id):
    run_battle_reset(loc_id)
    
    # Return fresh stats so the UI updates (e.g., health bars fill back up)
    parties = get_battle_participants(loc_id)
    char_stats = {}
    for p_name, members in parties.items():
        for c in members:
            hp = get_char_stat(c, 'hp')
            max_hp = get_char_stat(c, 'max_hp')
            char_stats[c.id] = {
                "hp": format_num(hp),
                "max_hp": format_num(max_hp),
                "is_dead": hp <= 0
            }
            
    return jsonify({
        "char_stats": char_stats,
        "log": [{"time": m.timestamp.strftime('%H:%M'), "text": m.message, "count": m.count} 
                for m in get_chronicle(12)]
    })
