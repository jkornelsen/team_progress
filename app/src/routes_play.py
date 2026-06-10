import logging
import json
from flask import (
    Blueprint, render_template, request, redirect, jsonify,
    g, session, current_app)
from http import HTTPStatus
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from app.models import (
    db, Entity, Item, Character, Location, Attrib, Event,
    Pile, AttribVal, Operation, OutcomeType, EventFactor,
    Recipe, RecipeAttribReq, LocDest, EventLink, EntityAbility, EventField,
    Progress, Overall, WinRequirement, GameMessage,
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
    do_effect_change, process_all_effects, format_for_display)
from .logic_progress import (
    tick_all_active, start_production, stop_production)
from .logic_production import (
    find_best_host, resolve_recipe_sources, can_perform_recipe,
    execute_production)
from .logic_navigation import (
    move_group, get_available_destinations, arrive_at_destination,
    is_in_grid, blocked_by_local_item, find_nearest_available_pos, is_adjacent)
from .logic_objectives import validate_requirements
from .logic_user_interaction import add_message
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
    
    # Tick All Production
    tick_all_active()

    # Fetch IDs of items currently being produced by the General Host
    items_in_production = {
        p.product_id for p in Progress.query.filter_by(
            game_token=game_token
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
        items_in_production=items_in_production,
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
    logger.debug(f"old_loc_id={id}")
    
    # 1. Fetch Characters & Items
    characters_here = Character.query.filter_by(
        game_token=game_token, location_id=id
    ).order_by(name_stripped()).all()
    
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
            # If out of bounds OR someone else already took this spot during the fix
            if not char.position or out_of_bounds or tuple(char.position) in occupied_in_fix:
                start_search = char.position or [1, 1]
                # Find nearest that isn't blocked by items/zones, 
                # and isn't in our 'occupied_in_fix' set
                new_pos = find_nearest_available_pos(location, start_search, exclude_char_id=char.id)
                
                # Check for collisions with characters we haven't processed yet
                while new_pos and tuple(new_pos) in occupied_in_fix:
                     # Bump the search slightly if there's a collision in the local tracker
                     new_pos = find_nearest_available_pos(location, [new_pos[0]+1, new_pos[1]], exclude_char_id=char.id)

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
    destinations = LocDest.query.filter(
        LocDest.game_token == game_token,
        ((LocDest.loc1_id == id) | 
         ((LocDest.loc2_id == id) & (LocDest.bidirectional == True)))
    ).all()
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

    # 6. Active Character Setup
    active_char_id = request.args.get('active_char_id', type=int)
    if not active_char_id and characters_here:
        active_char_id = characters_here[0].id

    return render_template(
        'play/location.html',
        location=location,
        inventory_piles=inventory_piles,
        characters_here=characters_here,
        destinations=destinations,
        grid_exits=grid_exits,
        referenced_items=referenced_data,
        attrib_values=sort_by_name_stripped(
            AttribVal.query.filter_by(game_token=game_token, subject_id=id).all(),
            lambda p: p.attrib),
        active_char_id=active_char_id,
        ctx_char=current_char,
        link_letters=LinkLetters(excluded='ctmoedw')
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
    party_criteria = []
    if character.travel_party:
        party_criteria.append(Character.travel_party == character.travel_party)
        party_criteria.append(Character.name == character.travel_party)
    party_criteria.append(Character.travel_party == character.name)

    if party_criteria:
        party_members = Character.query.filter(
            Character.game_token == game_token,
            Character.location_id == character.location_id,
            or_(*party_criteria),
            Character.id != character.id
        ).all()

    # Fetch Navigation (Nearby Destinations)
    destinations, has_nonadjacent = get_available_destinations(character)
    
    return render_template(
        'play/character.html',
        character=character,
        inventory=sort_by_name_stripped(
            Pile.query.filter_by(game_token=game_token, owner_id=id).all(),
            lambda p: p.item),
        attrib_values=sort_by_name_stripped(
            AttribVal.query.filter_by(game_token=game_token, subject_id=id).all(),
            lambda p: p.attrib),
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
    success, msg = transfer_item(
        item_id, from_owner_id=id, to_owner_id=char.location_id,
        quantity=qty, to_pos=char.position)
    
    item = Item.query.get((g.game_token, item_id))
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
    
    char = Character.query.get((g.game_token, id))
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
    
    item = Item.query.get((g.game_token, item_id))
    if success:
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
    success, msg = transfer_item(
        item_id, from_owner_id=id, to_owner_id=target_char_id,
        quantity=qty
    )

    item = Item.query.get((g.game_token, item_id))
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
        db.session.commit()
        return jsonify({"positions": results}), HTTPStatus.OK
    return jsonify({"message": results}), HTTPStatus.BAD_REQUEST

@play_bp.route('/char/<int:id>/go', methods=['POST'])
def char_travel(id):
    req = RequestHelper('form')
    dest_loc_id = req.get_int('dest_id')
    move_party = req.get_bool('move_party')
    success, message = arrive_at_destination(id, dest_loc_id, move_party)
    if success:
        db.session.commit()
        return '', HTTPStatus.NO_CONTENT
    return jsonify({"message": message}), HTTPStatus.BAD_REQUEST

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
    main_item = Item.query.get((game_token, item_id))
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
        host_id, recipe_id, owner_id, ctx, stop_at=stop_at)
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
    attribute = Attrib.query.get_or_404((game_token, attrib_id))
    subject = Entity.query.get_or_404((game_token, subject_id))
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
        
        if op == Operation.ASSIGN:
            new_val = req.get_float('value') or req.get_float('operand')
        else:
            operand = req.get_float('operand')
            if op == Operation.ADD:     new_val = val_record.value + operand
            elif op == Operation.SUB:   new_val = val_record.value - operand
            elif op == Operation.MULT:  new_val = val_record.value * operand
            elif op == Operation.DIV:   new_val = val_record.value / operand \
                                        if operand != 0 else val_record.value

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
            if attribute.is_binary:
                val_str = "ON" if new_val > 0 else "OFF"
            elif attribute.enum_list:
                try:
                    val_str = attribute.enum_list[int(new_val)]
                except:
                    val_str = f"{new_val:g}"
            else:
                val_str = f"{new_val:g}"
        else:
            val_str = f"{round(abs(operand), 2):g} = {round(new_val, 2):g}"
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
    event = Event.query.get_or_404((game_token, id))
    capture_origin(name=event.name)
    
    # Semantic Context
    subject_id = req.get_int('subject_role_id') or req.get_int('subject_id')
    subject = Entity.query.get(
        (game_token, subject_id)) if subject_id else None

    owner_id = req.get_int('owner_role_id') or req.get_int('owner_id')
    owner = Entity.query.get(
        (game_token, owner_id)) if owner_id else None

    ctx_char_id = req.get_int('target char_role_id') \
        or req.get_int('char_id') or session.get('old_char_id')
    ctx_char = Character.query.get(
        (game_token, ctx_char_id)) if ctx_char_id else None

    ctx_loc_id = req.get_int('at_role_id') \
        or req.get_int('loc_id') or session.get('old_loc_id')
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
    ctx_loc = Location.query.get(
        (game_token, ctx_loc_id)) if ctx_loc_id else None
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
        if role == Participant.BLUEPRINT:
            continue
        if role == Participant.UNIVERSAL:
            search_pool= [Entity.query.get((game_token, GENERAL_ID))]
        elif role == Participant.SUBJECT:
            search_pool = [subject] if subject else other_entities_here
        elif role == Participant.OWNER:
            search_pool = [owner] if owner else other_entities_here
        elif role == Participant.AT:
            search_pool = [ctx_loc] if ctx_loc else other_entities_here
        else:
            search_pool = other_entities_here
        logger.debug(f"routes_play: search_pool for {role} = {[e.name for e in search_pool]}")

        role_candidates = set(search_pool)
        factors = [
            f for f in event.factors if f.infield and f.infield.role == role]
        for factor in factors:
            can_use = {
                ent for ent in search_pool 
                if is_factor_met(factor, ent, subject_id=subject_id,
                require_comparison=(factor.usage_type == Participant.DET))
            }
            role_candidates &= can_use # Intersection
            if not can_use:
                if role not in fields_not_met:
                    fields_not_met[role] = {'positive': [], 'negated': []}
                logic_key = 'negated' if factor.negate else 'positive'
                field = factor.infield
                fields_not_met[role][logic_key].append((factor, field))

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
            if field.attrib_id:
                ent = Attrib.query.get((game_token, field.attrib_id))
            elif field.item_id:
                ent = Item.query.filter_by(game_token=game_token, id=field.item_id, masked=False).first()
            elif field.char_id:
                ent = Character.query.get((game_token, field.char_id))
            elif field.recipe_id:
                rec = Recipe.query.get((game_token, field.recipe_id))
                ent = Item.query.filter_by(game_token=game_token, id=rec.product_id, masked=False).first() if rec else None
            else:
                ent = None
            if ent:
                all_related[ent.id] = ent

    # Chained events
    for link in event.chained:
        ent = link.child
        all_related[ent.id] = ent

    related = sort_by_name_stripped(list(all_related.values()))

    return render_template(
        'play/event.html',
        event=event,
        subject=subject,
        ctx_char=ctx_char,
        ctx_loc=ctx_loc,
        role_entities=eligible_role_entities,
        fields_not_met=fields_not_met,
        related_entities=related,
        OutcomeType=OutcomeType,
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
    event = Event.query.get((game_token, id))
    req = RequestHelper('form')
    
    role_entities = {}
    for key in req:
        if key.endswith(Participant.ROLE_SUFFIX):
            role_name = Participant.formkey_to_role(key)
            role_entities[role_name] = req.get_int(key)
    
    modifiers = calculate_determinants(event, role_entities)
    effect_previews = preview_effects(event, role_entities)
    return jsonify({
        "modifiers": modifiers,
        "effect_previews": effect_previews
    })

@play_bp.route('/event/roll/<int:id>', methods=['POST'])
def roll_event(id):
    game_token = g.game_token
    event = Event.query.get_or_404((game_token, id))
    req = RequestHelper('form')

    role_entities = {}
    for key in req:
        if key.endswith(Participant.ROLE_SUFFIX):
            role_name = Participant.formkey_to_role(key)
            role_entities[role_name] = req.get_int(key)

    tier = None
    if event.outcome_type == OutcomeType.ROLLER:
        n_dice = req.get_int('num_dice', 1)
        sides = req.get_int('sides', 20)
        bonus = req.get_int('bonus', 0)
        result_val, result_str = roll_for_system_outcome(
            id, n_dice, sides, bonus)
    else:
        difficulty = req.get_float('difficulty', 0.55)
        result_val, result_str, tier = roll_for_outcome(
            id, role_entities, difficulty)

    resolved_effects, ledger = resolve_effects(
        event, role_entities, result_val, tier)
    process_all_effects(
        event, role_entities, result_val, tier, force_auto_only=True)
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
    eff = EventFactor.query.get_or_404(factor_id)
    
    role_entities = {
        Participant.formkey_to_role(k): req.get_int(k)
        for k in req if k.endswith(Participant.ROLE_SUFFIX)
    }
    try:
        roll_str = req.get_str('roll_value')
        roll_val = json.loads(roll_str)
    except (json.JSONDecodeError, TypeError):
        roll_val = req.get_float('roll_value')
    success, message = do_effect_change(
        eff, roll_val, role_entities)
    db.session.commit()
    
    if not success:
        return jsonify({"message": message}), HTTPStatus.BAD_REQUEST

    return '', HTTPStatus.NO_CONTENT

