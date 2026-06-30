import logging
import json
from flask import (
    Blueprint, request, session, flash, redirect, url_for, render_template,
    g, jsonify)
from http import HTTPStatus
from sqlalchemy import select, delete, or_
from app.models import (
    GENERAL_ID, EQUIPMENT_SLOTS_ID, StorageType, ENTITIES, db,
    Entity, Item, Character, Location, Attrib, Event, 
    Pile, ItemLimit, AttribVal, EnumEntry, Operation, EntityAbility,
    Recipe, RecipeSource, RecipeByproduct, RecipeAttribReq,
    LocDest, LocZone, EntranceReq, ItemRef,
    Participant, OutcomeType, SuccessTier, EventFactor, EventField, EventLink,
    Scenario, WinRequirement, IdSequence)
from app.serialization import clone_entity
from app.utils import (
    LinkLetters, RequestHelper, parse_coords,
    capture_origin, redirect_back, name_stripped, sort_by_name_stripped)
from .logic_discovery import run_discovery_scan

logger = logging.getLogger(__name__)
configure_bp = Blueprint('configure', __name__, url_prefix='/configure')

# ------------------------------------------------------------------------
# Main Index
# ------------------------------------------------------------------------

@configure_bp.route('/')
def index():
    """Lists all entities grouped by type."""
    capture_origin("Main Setup")

    entities = {
        name: model.query.filter_by(game_token=g.game_token).order_by(name_stripped()).all()
        for name, model in ENTITIES.items()
    }
    
    return render_template(
        'configure/index.html',
        **entities
    )

# ------------------------------------------------------------------------
# Scenario Settings
# ------------------------------------------------------------------------

@configure_bp.route('/scenario', methods=['GET', 'POST'])
def edit_scenario():
    game_token = g.game_token
    scenario = db.session.get(Scenario, game_token)
    slots_attrib = db.session.get(Attrib, (game_token, EQUIPMENT_SLOTS_ID))

    if request.method == 'POST':
        req = RequestHelper('form')
        scenario.title = req.get_str('title', scenario.title)
        scenario.description = req.get_str('description')
        
        use_slots = req.get_bool('use_slots')
        if use_slots:
            if not slots_attrib:
                # Create the reserved attribute with defaults
                slots_attrib = Attrib(
                    id=EQUIPMENT_SLOTS_ID,
                    game_token=game_token, 
                    name="Equipment Slots",
                    description="Equipment slots for characters."
                )
                db.session.add(slots_attrib)
                slots_attrib.enum_entries = [
                    EnumEntry(
                        game_token=game_token,
                        label="Main Hand",
                        order_index=0),
                    EnumEntry(
                        game_token=game_token,
                        label="Body",
                        order_index=1)
                ]
        elif slots_attrib:
            db.session.delete(slots_attrib)

        # Save Win Conditions
        db.session.execute(delete(WinRequirement).filter_by(
            game_token=game_token))
        win_req_rows = req.get_list('winreqs')
        win_req_rows.sort(key=lambda r: r.get_int('order_index', 0))
        for idx, row in enumerate(win_req_rows):
            target_id = row.get_int('target_id')
            owner_id = row.get_int('owner_id', None)
            attrib_id = row.get_int('attrib_id', None)
            val = row.get_float('quantity')
            if not target_id and not attrib_id:
                continue
            new_req = WinRequirement(
                game_token=game_token,
                order_index=idx
            )
            
            # Resolve target
            if target_id:
                target = db.session.get(Entity, (game_token, target_id))
                if target:
                    if target.entity_type == Item.TYPENAME:
                        new_req.item_id = target_id
                    elif target.entity_type == Character.TYPENAME:
                        new_req.char_id = target_id

            # Resolve owner
            if owner_id:
                owner = db.session.get(Entity, (game_token, owner_id))
                if owner:
                    if owner.entity_type == Location.TYPENAME:
                        new_req.loc_id = owner_id
                    elif owner.entity_type == Character.TYPENAME:
                        if not new_req.char_id:
                            new_req.char_id = owner_id

            # Resolve field
            if attrib_id:
                new_req.attrib_id = attrib_id
                new_req.attrib_value = val
            else:
                new_req.quantity = val

            db.session.add(new_req)

        db.session.commit()
        return redirect_back('configure.index')
        
    entities = {
        name: model.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all()
        for name, model in ENTITIES.items()
    }
    return render_template(
        'configure/scenario.html',
        scenario=scenario,
        slots_attrib=slots_attrib,
        **entities
    )

@configure_bp.route('/cancel')
def cancel():
    """Generic cancel route to return to the last known origin."""
    return redirect_back('configure.index', fallback_to_referrer=False)

# ------------------------------------------------------------------------
# Entity Settings
# ------------------------------------------------------------------------

@configure_bp.route('/item/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/item/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_item(id):
    game_token = g.game_token
    item = Item.get_or_new(game_token, id)

    if request.method == 'POST':
        req = RequestHelper('form')

        if 'delete' in request.form:
            db.session.delete(item)
            db.session.commit()
            return redirect_back('configure.index')

        if item.id is None:
            item.id = IdSequence.generate_next_id(g.game_token)
            db.session.add(item)
        
        item.name = req.get_str('name', item.name)
        item.description = req.get_str('description')
        
        val = req.get_str('storage_type')
        item.storage_type = (
            val if val in StorageType.ALL_CODES else StorageType.UNIVERSAL)

        item.q_limit = req.get_float('q_limit')
        db.session.execute(delete(ItemLimit).filter_by(
            game_token=game_token, item_id=item.id))
        for limit_row in req.get_list('limits_for'):
            owner_id = limit_row.get_int('owner_id')
            q_limit = limit_row.get_float('q_limit')
            if owner_id:
                db.session.add(ItemLimit(
                    game_token=game_token,
                    item_id=item.id,
                    owner_id=owner_id,
                    q_limit=q_limit
                ))
        item.slot_id = req.get_int('slot_id', None) \
            if item.storage_type == StorageType.CARRIED else None

        item.toplevel = 'toplevel' in request.form
        item.loc_hosted = 'loc_hosted' in request.form
        item.masked = 'masked' in request.form
        
        if item.storage_type == StorageType.UNIVERSAL:
            qty = req.get_float('quantity')
            gen_pile = Pile.query.filter_by(
                game_token=game_token, item_id=item.id, owner_id=GENERAL_ID
            ).first()
            if qty:
                if not gen_pile:
                    gen_pile = Pile(
                        game_token=game_token, item_id=item.id, 
                        owner_id=GENERAL_ID, position=None
                    )
                    db.session.add(gen_pile)
                gen_pile.quantity = qty
            elif gen_pile:
                db.session.delete(gen_pile)

        # Attribute Values
        db.session.execute(delete(AttribVal).filter_by(
            game_token=game_token, subject_id=item.id))
        for row in req.get_list('attribs'):
            attr_id = row.get_int('id')
            if attr_id:
                db.session.add(AttribVal(
                    game_token=game_token, subject_id=item.id,
                    attrib_id=attr_id, value=row.get_float('value')
                ))

        # Events
        db.session.execute(delete(EntityAbility).filter_by(
            game_token=game_token, entity_id=item.id))
        for row in req.get_list('abilities'):
            event_id = row.get_int('id')
            if event_id:
                db.session.add(EntityAbility(
                    game_token=game_token, entity_id=item.id, event_id=event_id
                ))

        recipe_rows = req.get_list('recipes')
        recipe_rows.sort(key=lambda r: r.get_int('order_index', 0))
        item.recipes = []
        for order_index, recipe_row in enumerate(recipe_rows):
            if not recipe_row:
                continue

            recipe_id = recipe_row.get_int('id', None)
            if recipe_id is None:
                recipe_id = IdSequence.generate_next_id(game_token)

            recipe = Recipe(
                game_token=game_token,
                id=recipe_id,
                product_id=item.id,
                order_index=order_index,
                rate_amount=recipe_row.get_float('rate_amount', 1.0),
                rate_duration=max(1, recipe_row.get_int('rate_duration', 3)),
                instant=recipe_row.get_bool('instant')
            )

            for source_row in recipe_row.get_list('sources'):
                if not source_row:
                    continue
                item_id = source_row.get_int('item_id')
                if item_id:
                    recipe.sources.append(RecipeSource(
                        game_token=game_token,
                        recipe_id=recipe_id,
                        item_id=item_id,
                        q_required=source_row.get_float('q_required', 0.0),
                        preserve=source_row.get_bool('preserve')
                    ))

            for byproduct_row in recipe_row.get_list('byproducts'):
                if not byproduct_row:
                    continue
                item_id = byproduct_row.get_int('item_id')
                if item_id:
                    recipe.byproducts.append(RecipeByproduct(
                        game_token=game_token,
                        recipe_id=recipe_id,
                        item_id=item_id,
                        rate_amount=byproduct_row.get_float('rate_amount')
                    ))

            for attrib_row in recipe_row.get_list('attrib_reqs'):
                if not attrib_row:
                    continue
                attrib_id = attrib_row.get_int('attrib_id')
                if attrib_id:
                    recipe.attrib_reqs.append(RecipeAttribReq(
                        game_token=game_token,
                        recipe_id=recipe_id,
                        attrib_id=attrib_id,
                        op_compare=attrib_row.get_str('op_compare'),
                        val_required=attrib_row.get_float('val_required')
                    ))

            item.recipes.append(recipe)

        db.session.commit()

        run_discovery_scan(game_token)

        if 'duplicate' in request.form:
            return duplicate_entity(item.id, 'item')
            
        return redirect_back('configure.index') 

    # GET: Prepare variables for the template
    gen_qty = 0
    if item.id is not None:
        gen_pile = Pile.query.filter_by(
            game_token=game_token, item_id=item.id, owner_id=GENERAL_ID
        ).first()
        gen_qty = gen_pile.quantity if gen_pile else 0

    slots_attrib = db.session.get(Attrib, (game_token, EQUIPMENT_SLOTS_ID))
    all_slots = [
        e for e in slots_attrib.enum_entries
        ] if slots_attrib else []

    return render_template('configure/item.html', 
        item=item, 
        initial_qty=gen_qty,
        all_items=Item.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_attribs=Attrib.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_chars=Character.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_locs=Location.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_events=Event.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        recipes=item.recipes if item else [],
        all_slots=all_slots,
        Operation=Operation,
    )

@configure_bp.route('/location/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/location/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_location(id):
    game_token = g.game_token
    loc = Location.get_or_new(game_token, id)

    if request.method == 'POST':
        req = RequestHelper('form')

        if 'delete' in request.form:
            handle_deletion(loc)
            return redirect_back('configure.index')

        if loc.id is None:
            loc.id = IdSequence.generate_next_id(g.game_token)
            db.session.add(loc)

        loc.name = req.get_str('name', loc.name)
        loc.description = req.get_str('description')
        loc.dimensions = parse_coords(req.get_str('dimensions_str'))
        loc.toplevel = 'toplevel' in request.form
        loc.masked = 'masked' in request.form

        # Destinations (Exits) -- Symmetric Route Saving
        # Fetch existing routes involving this location to determine what to delete/update
        existing_routes = LocDest.query.filter(
            (LocDest.game_token == game_token) & 
            ((LocDest.loc1_id == loc.id) | (LocDest.loc2_id == loc.id))
        ).all()
        existing_map = {r.id: r for r in existing_routes}
        
        submitted_ids = []
        new_coords = set()
        for row in req.get_list('dests'):
            target_id = row.get_int('target_id')
            if not target_id: continue
            route_id = row.get_int('id')
            direction = row.get_str('direction', 'two-way')
            
            existing_route = existing_map.get(route_id)
            old_door_there = None
            if existing_route:
                route = existing_route
                old_door_there = route.door_at(target_id)
            else:
                route = LocDest(game_token=game_token)
                db.session.add(route)

            door_here = row.get_coords('door_here')
            bidirectional = (direction == 'two-way')
            here_is_loc1 = (direction != 'backward')
            if loc.id < target_id:
                route.loc1_id, route.loc2_id = loc.id, target_id
                if here_is_loc1:
                    route.door1, route.door2 = door_here, old_door_there
                else:
                    route.door1, route.door2 = old_door_there, door_here
            else:
                route.loc1_id, route.loc2_id = target_id, loc.id
                if here_is_loc1:
                    route.door1, route.door2 = old_door_there, door_here
                else:
                    route.door1, route.door2 = door_here, old_door_there
            route.bidirectional = bidirectional

            db.session.flush()

            d1_tuple = tuple(route.door1) if route.door1 else None
            if d1_tuple and d1_tuple in new_coords:
                route.door1 = None
                target_loc = db.session.get(Location, (game_token, target_id))
                target_name = target_loc.name if target_loc else "other location"
                flash(
                    f"Entrance at {target_name} was reset"
                    " because there is another door at that position.",
                    "warning")
            elif d1_tuple:
                new_coords.add(d1_tuple)

            submitted_ids.append(route.id)

        # Cleanup: Delete routes that were removed from the UI
        for rid, r_obj in existing_map.items():
            if rid not in submitted_ids:
                db.session.delete(r_obj)

        # Items on Ground
        db.session.execute(delete(Pile).filter_by(
            game_token=game_token, owner_id=loc.id))
        for row in req.get_list('items'):
            item_id = row.get_int('item_id')
            if item_id:
                db.session.add(Pile(
                    game_token=game_token,
                    owner_id=loc.id,
                    item_id=item_id,
                    quantity=row.get_float('quantity'),
                    position=row.get_coords('pos')
                ))

        # Item Refs
        db.session.execute(delete(ItemRef).filter_by(
            game_token=game_token, loc_id=loc.id))
        for ref_id in request.form.getlist('item_refs[]'):
            if ref_id:
                db.session.add(ItemRef(
                    game_token=game_token, 
                    loc_id=loc.id, 
                    item_id=int(ref_id)
                ))

        # Zones
        db.session.execute(delete(LocZone).filter_by(
            game_token=game_token, loc_id=loc.id))
        zone_rows = req.get_list('zones')
        zone_rows.sort(key=lambda row: row.get_int('order_index', 0))
        for idx, row in enumerate(zone_rows):
            lt = row.get_coords('lt')
            rb = row.get_coords('rb')
            if lt and rb:
                coords = lt + rb
                db.session.add(LocZone(
                    game_token=game_token,
                    loc_id=loc.id,
                    coords=coords,
                    label=row.get_str('label'),
                    color=row.get_str('color'),
                    prevents_travel=row.get_bool('prevents_travel'),
                    order_index=idx
                ))

        # Entrance Requirements
        db.session.execute(delete(EntranceReq).filter_by(
            game_token=game_token, loc_id=loc.id))
        for row in req.get_list('entrance_reqs'):
            entity_id = row.get_int('entity_id', None)
            attrib_id = row.get_int('attrib_id', None)
            val_required = row.get_float('val_required')
            if entity_id or attrib_id:
                new_req = EntranceReq(
                    game_token=game_token,
                    loc_id=loc.id,
                    item_id=entity_id,
                    attrib_id=attrib_id,
                    val_required=val_required
                )
                db.session.add(new_req)

        # Local Events
        db.session.execute(delete(EntityAbility).filter_by(
            game_token=game_token, entity_id=loc.id))
        for row in req.get_list('events'):
            event_id = row.get_int('id')
            if event_id:
                db.session.add(EntityAbility(
                    game_token=game_token,
                    entity_id=loc.id,
                    event_id=event_id
                ))

        # Attribute Values
        db.session.execute(delete(AttribVal).filter_by(
            game_token=game_token, subject_id=loc.id))
        for row in req.get_list('attribs'):
            attr_id = row.get_int('id')
            if attr_id:
                db.session.add(AttribVal(
                    game_token=game_token,
                    subject_id=loc.id,
                    attrib_id=attr_id,
                    value=row.get_float('value')
                ))

        db.session.commit()

        run_discovery_scan(game_token)

        if 'duplicate' in request.form:
            return duplicate_entity(loc.id, 'location')
        return redirect_back('configure.index') 

    routes = LocDest.query.filter(
        (LocDest.game_token == game_token) & 
        ((LocDest.loc1_id == id) | (LocDest.loc2_id == id))
    ).all()
    normalized_dests = []
    for r in routes:
        target = r.other_loc(id)
        if not target: continue
        if r.bidirectional:
            direction = 'two-way'
        else:
            direction = 'forward' if r.loc1_id == id else 'backward'
        normalized_dests.append({
            'id': r.id,
            'target_id': target.id,
            'door_here': r.door_at(id),
            'direction': direction
        })

    return render_template('configure/location.html', 
        location=loc,
        destinations=normalized_dests,
        inventory=Pile.query.filter_by(
            game_token=game_token, owner_id=id).all() if id != 'new' else [],
        all_locs=Location.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_items=Item.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_attribs=Attrib.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_events=Event.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all()
    )

@configure_bp.route('/character/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/character/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_character(id):
    game_token = g.game_token
    char = Character.get_or_new(game_token, id)

    if request.method == 'POST':
        req = RequestHelper('form')

        if 'delete' in request.form:
            handle_deletion(char)
            return redirect_back('configure.index')

        if char.id is None:
            char.id = IdSequence.generate_next_id(g.game_token)
            db.session.add(char)

        char.name = req.get_str('name', char.name)
        char.description = req.get_str('description')
        char.location_id = req.get_int('location_id', None)
        char.position = parse_coords(req.get_str('pos_str'))
        char.travel_party = req.get_str('travel_party')
        char.toplevel = 'toplevel' in request.form

        # Update Attrib values
        db.session.execute(delete(AttribVal).filter_by(
            game_token=game_token, subject_id=char.id))
        for attrib_row in req.get_list('attribs'):
            attr_id = attrib_row.get_int('id')
            if attr_id:
                val = attrib_row.get_float('value')
                db.session.add(
                    AttribVal(
                        game_token=game_token,
                        subject_id=char.id,
                        attrib_id=attr_id,
                        value=val))

        # Inventory
        db.session.execute(delete(Pile).filter_by(
            game_token=game_token, owner_id=char.id))
        for item_row in req.get_list('items'):
            item_id = item_row.get_int('item_id')
            if item_id:
                db.session.add(Pile(
                    game_token=game_token,
                    owner_id=char.id,
                    item_id=item_id,
                    quantity=item_row.get_float('quantity'),
                    slot_id=item_row.get_int('slot_id', None),
                ))

        # Abilities
        db.session.execute(delete(EntityAbility).filter_by(
            game_token=game_token, entity_id=char.id))
        for ability_row in req.get_list('abilities'):
            event_id = ability_row.get_int('id')
            if event_id:
                db.session.add(
                    EntityAbility(
                        game_token=game_token,
                        entity_id=char.id,
                        event_id=event_id
                    )
                )

        db.session.commit()

        run_discovery_scan(game_token)

        if 'duplicate' in request.form:
            return duplicate_entity(char.id, 'character')
        return redirect_back('configure.index') 

    slots_attrib = db.session.get(Attrib, (game_token, EQUIPMENT_SLOTS_ID))
    all_slots = [
        e for e in slots_attrib.enum_entries
        ] if slots_attrib else []

    return render_template('configure/character.html', 
        character=char, 
        all_locs=Location.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_attribs=Attrib.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_items=Item.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_events=Event.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_slots=all_slots
    )

@configure_bp.route('/attrib/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/attrib/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_attrib(id):
    game_token = g.game_token
    attrib = Attrib.get_or_new(game_token, id)

    if request.method == 'POST':
        req = RequestHelper('form')

        if 'delete' in request.form:
            handle_deletion(attrib)
            return redirect_back('configure.index')

        if attrib.id is None:
            attrib.id = IdSequence.generate_next_id(g.game_token)
            db.session.add(attrib)

        attrib.name = req.get_str('name', attrib.name)
        attrib.description = req.get_str('description')
        
        v_type = req.get_str('value_type')
        attrib.is_binary = (v_type == 'binary')

        existing_entries = {e.id: e for e in attrib.enum_entries}
        enum_entries = []
        if v_type == 'enum':
            for idx, row in enumerate(req.get_list('enum_entries')):
                eid = row.get_int('id')
                label = row.get_str('label')
                if not label: continue

                if eid in existing_entries:
                    # Reuse so we don't break ID references
                    entry = existing_entries[eid]
                    entry.label = label
                    entry.order_index = idx
                    enum_entries.append(entry)
                else:
                    enum_entries.append(EnumEntry(
                        game_token=game_token, 
                        label=label, 
                        order_index=idx
                    ))

        attrib.enum_entries = enum_entries

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(attrib.id, 'attrib')
        return redirect_back('configure.index') 

    return render_template('configure/attrib.html', attrib=attrib)

@configure_bp.route('/event/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/event/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_event(id):
    game_token = g.game_token
    event = Event.get_or_new(game_token, id)

    if request.method == 'POST':
        req = RequestHelper('form')

        if 'delete' in request.form:
            handle_deletion(event)
            return redirect_back('configure.index')

        if event.id is None:
            event.id = IdSequence.generate_next_id(g.game_token)
            db.session.add(event)

        event.name = req.get_str('name', event.name)
        event.description = req.get_str('description')
        event.toplevel = 'toplevel' in request.form
        event.outcome_type = req.get_str('outcome_type')

        if event.outcome_type == OutcomeType.ROLLER:
            event.roller_type = req.get_str('roller_type')
        elif event.outcome_type in [OutcomeType.FOURWAY, OutcomeType.NUMERIC]:
            event.numeric_range = [
                req.get_int('range_min'), 
                req.get_int('range_max')
            ]
        elif event.outcome_type == OutcomeType.DETERMINED:
            event.fixed_base = req.get_float('fixed_base')
        elif event.outcome_type == OutcomeType.SELECT:
            event.selection_attrib_id = req.get_int(
                'selection_attrib_id', None)

        # --- SAVE DETERMINANTS & EFFECTS ---
        # 1. Clear existing factors to perform a clean sync
        # (This is simpler than matching IDs for small lists)
        event.factors = [] 

        # 2. Process Determinants and Effects
        for usage in [Participant.DET, Participant.EFF]:
            for row in req.get_list(f"{usage}s"):
                op_app = row.get_str('op_application')
                op_trans = row.get_str('op_transform')
                get_val_from = row.get_str('get_val_from', Participant.INFIELD)
                if op_trans == Operation.CONST and usage == Participant.EFF:
                    get_val_from = Participant.OUTCOME
                outcome_success = SuccessTier.ALWAYS
                if event.outcome_type == OutcomeType.FOURWAY:
                    outcome_success = row.get_str(
                        'outcome_success', SuccessTier.ALWAYS)

                factor = EventFactor(
                    game_token=game_token,
                    event_id=event.id,
                    usage_type=usage,
                    order_index=row.get_int('order_index'),
                    label=row.get_str('label'),
                    get_val_from=get_val_from,
                    negate=row.get_bool('negate'),
                    outcome_success=outcome_success,
                    auto_apply=row.get_bool('auto_apply'),
                    op_application=op_app,
                    op_transform=op_trans,
                    val_transform=row.get_float('val_transform', 1.0),
                    val_required=row.get_float('val_required', 1.0)
                )
                for field_key in ('infield', 'outfield'):
                    if usage == Participant.DET and op_app in (
                            Operation.ASSIGN, Operation.MEM_STORE):
                        continue
                    if field_key == 'infield' and \
                            factor.get_val_from != Participant.INFIELD:
                        factor.infield = None
                        continue
                    elif field_key == 'outfield' and usage == Participant.DET:
                        factor.outfield = None
                        continue
                    fld = row.get_map(field_key)
                    mode = fld.get_str('field_mode')
                    role = fld.get_str('role')
                    if mode in Participant.USES_BLUEPRINT:
                        role = Participant.BLUEPRINT
                    if role and mode:
                        fld_attrib_id = fld.get_int('attrib_id') \
                            if mode in Participant.USES_ATTRIB else None
                        fld_item_id = fld.get_int('item_id') if (
                            mode in Participant.USES_ITEM or 
                                (mode == Participant.ATTR and
                                role == Participant.UNIVERSAL)
                            ) else None
                        fld_recipe_id = fld.get_int('recipe_id') \
                            if mode in Participant.USES_RECIPE else None
                        fld_loc_id = fld.get_int('loc_id') \
                            if mode in Participant.USES_LOC else None
                        fld_source_item_id = fld.get_int('source_item_id') \
                            if mode in Participant.USES_SOURCE_ITEM else None

                        setattr(factor, field_key, EventField(
                            game_token=game_token,
                            role=role,
                            field_mode=mode,
                            child_of_anchor=fld.get_bool('child_of_anchor'),
                            attrib_id=fld_attrib_id,
                            item_id=fld_item_id,
                            recipe_id=fld_recipe_id,
                            loc_id=fld_loc_id,
                            source_item_id=fld_source_item_id,
                        ))
                    else:
                        setattr(factor, field_key, None)
                event.factors.append(factor)

        event.chained = []
        for row in req.get_list('chained'):
            child_id = row.get_int('child_id')
            if child_id:
                instance_args = {
                    "game_token": game_token,
                    "event_id": event.id,
                    "usage_type": Participant.CHAIN
                }
                if event.outcome_type == OutcomeType.FOURWAY:
                    instance_args['outcome_success'] = row.get_str(
                        'outcome_success', SuccessTier.ALWAYS)
                get_val_from = row.get_str('get_val_from')
                if get_val_from == Participant.OUTCOME:
                    instance_args.update({
                        "get_val_from": get_val_from,
                        "op_application": row.get_str(
                            'op_application', Operation.EQ),
                        "val_required": row.get_float(
                            'val_required', 1.0),
                        "negate": row.get_bool('negate')
                    })
                elif get_val_from == Participant.INFIELD:
                    fld = row.get_map('infield')
                    mode = fld.get_str('field_mode')
                    role = fld.get_str('role')
                    if role and mode:
                        instance_args.update({
                            "get_val_from": get_val_from,
                            "op_application": row.get_str(
                                'op_application', Operation.EQ),
                            "op_transform": row.get_str('op_transform'),
                            "val_transform": row.get_float(
                                'val_transform', 1.0),
                            "val_required": row.get_float(
                                'val_required', 1.0),
                            "negate": row.get_bool('negate')
                        })
                        fld_attrib_id = fld.get_int('attrib_id') \
                            if mode in Participant.USES_ATTRIB else None
                        fld_item_id = fld.get_int('item_id') if (
                            mode in Participant.USES_ITEM or 
                                (mode == Participant.ATTR and
                                role == Participant.UNIVERSAL)
                            ) else None
                        instance_args['infield'] = EventField(
                            game_token=game_token,
                            role=role,
                            field_mode=mode,
                            child_of_anchor=fld.get_bool('child_of_anchor'),
                            attrib_id=fld_attrib_id,
                            item_id=fld_item_id
                        )

                factor = EventFactor(**instance_args)
                elink = EventLink(
                    game_token=game_token,
                    parent_id=event.id,
                    child_id=child_id,
                    req=factor
                )
                event.chained.append(elink)

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(event.id, 'event')
        return redirect_back('configure.index') 

    # Data for select boxes
    all_items = Item.query.filter_by(
        game_token=game_token).order_by(name_stripped()).all()
    recipe_map = {}
    for item in all_items:
        recipe_map[item.id] = [
            {
                'id': r.id,
                'summary': r.summary,
                'sources': [{'id': s.item_id, 'name': s.ingredient.name}
                             for s in r.sources],
                'byproducts': [{'id': b.item_id, 'name': b.item.name}
                                for b in r.byproducts],
            }
            for r in item.recipes
        ]

    return render_template('configure/event.html', 
        event=event,
        all_attribs=Attrib.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_items=all_items,
        all_locs=Location.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        all_events=Event.query.filter_by(
            game_token=game_token).order_by(name_stripped()).all(),
        recipe_map=recipe_map,
        OutcomeType=OutcomeType,
        SuccessTier=SuccessTier,
        Operation=Operation,
        Participant=Participant
    )

# ------------------------------------------------------------------------
# Lookup
# ------------------------------------------------------------------------

@configure_bp.route('/lookup/<string:ent_type>/<int:id>')
def lookup(ent_type, id):
    game_token = g.game_token
    entity = db.get_or_404(Entity, (game_token, id))
    
    results = {}  # dict of lists

    def sort_results(r_list):
        r_list[:] = sort_by_name_stripped(r_list)

    if ent_type == Item.TYPENAME:
        # Who has this item
        piles = Pile.query.filter_by(game_token=game_token, item_id=id).all()
        key_name = 'Physical Presence'
        results[key_name] = []
        for p in piles:
            owner = db.session.get(Entity, (game_token, p.owner_id))
            label = "General Storage" if owner.id == GENERAL_ID else f"Stored at ({owner.entity_type})"
            results[key_name].append({
                'label': label,
                'name': owner.name,
                'link': url_for(f'play.play_{owner.entity_type}', id=owner.id),
                'value': f'Qty: {p.quantity}'
            })
        sort_results(results[key_name])

        # Recipe dependencies
        sources = RecipeSource.query.filter_by(game_token=game_token, item_id=id).all()
        key_name = 'Used as Ingredient'
        results[key_name] = []
        for s in sources:
            recipe = db.session.get(Recipe, (game_token, s.recipe_id))
            prod = db.session.get(Item, (game_token, recipe.item_id))
            results[key_name].append({
                'label': 'Required to produce',
                'name': prod.name,
                'link': url_for('play.play_item', id=prod.id),
                'value': f'Needs {s.q_required}'
            })
        sort_results(results[key_name])

    elif ent_type == Attrib.TYPENAME:
        # Who uses this attribute
        stmt = select(AttribVal).filter_by(game_token=game_token, attrib_id=id)
        attrib_vals = db.session.execute(stmt).scalars().all()
        key_name = 'Applied to Entities'
        results[key_name] = []
        for av in attrib_vals:
            subject = db.session.get(Entity, (game_token, av.subject_id))
            results[key_name].append({
                'label': f'Stat on {subject.entity_type}',
                'name': subject.name,
                'link': url_for(f'play.play_{subject.entity_type}', id=subject.id),
                'value': f'{av.display}'
            })
        sort_results(results[key_name])

        # Events that use this attribute
        stmt = (
            select(Event)
            .join(EventFactor, (Event.id == EventFactor.event_id) &
                               (Event.game_token == EventFactor.game_token))
            .join(EventField, or_(EventFactor.infield_id == EventField.id,
                                  EventFactor.outfield_id == EventField.id))
            .where(
                EventField.game_token == game_token,
                EventField.attrib_id == id
            )
            .distinct()
        )
        events = db.session.execute(stmt).scalars().all()
        key_name = 'Used in Events'
        results[key_name] = []
        for evt in events:
            results[key_name].append({
                'label': f'Used in event',
                'name': evt.name,
                'link': url_for(f'play.play_event', id=evt.id)
            })
        sort_results(results[key_name])

    elif ent_type == Location.TYPENAME:
        # What links to this location
        dests = LocDest.query.filter(
            LocDest.game_token == game_token,
            (LocDest.loc1_id == id) | (LocDest.loc2_id == id)
        ).all()
        key_name = 'Destinations'
        results[key_name] = []
        for d in dests:
            other_id = d.loc2_id if d.loc1_id == id else d.loc1_id
            other = db.session.get(Location, (game_token, other_id))
            results[key_name].append({
                'label': 'Linked to',
                'name': other.name,
                'link': url_for('play.play_location', id=other.id)
            })
        sort_results(results[key_name])

    elif ent_type == Event.TYPENAME:
        # Who can trigger this
        stmt = (
            select(EntityAbility, Entity)
            .join(Entity, (EntityAbility.entity_id == Entity.id) & 
                          (EntityAbility.game_token == Entity.game_token))
            .where(
                EntityAbility.game_token == game_token,
                EntityAbility.event_id == id
            )
            .order_by(name_stripped(Entity.name))
        )
        rows = db.session.execute(stmt).all()
        if rows:
            key_name = 'Can Be Called By'
            results[key_name] = []
            for _, owner in rows:
                results[key_name].append({
                    'label': f'Ability on {owner.entity_type}',
                    'name': owner.name,
                    'link': url_for(f'play.play_{owner.entity_type}', id=owner.id),
                })
            sort_results(results[key_name])

    # --- GLOBAL DESCRIPTION SCAN (For Markdown Links) ---
    # This handles things like [Red Bar Rate](/play/event/46)
    mention_key = 'Mentioned in Descriptions'
    search_str = f'/{ent_type}/{id}'
    
    # Check Scenario description
    ov = db.session.get(Scenario, game_token)
    if ov.description and search_str in ov.description:
        results.setdefault(mention_key, []).append({
            'label': 'Scenario Settings',
            'name': ov.title,
            'link': url_for('configure.edit_scenario'),
        })

    # Check all Entity descriptions
    all_ents = Entity.query.filter_by(game_token=game_token).all()
    for ent in all_ents:
        if ent.id == id and ent.entity_type == ent_type:
            continue # Don't list self
        if ent.description and search_str in ent.description:
            if ent.entity_type == Attrib.TYPENAME:
                link = url_for(f'configure.edit_attrib', id=ent.id)
            else:
                link = url_for(f'play.play_{ent.entity_type}', id=ent.id)
            results.setdefault(mention_key, []).append({
                'label': f'{ent.entity_type.capitalize()} Desc',
                'name': ent.name,
                'link': link
            })
    if mention_key in results:
        sort_results(results[mention_key])

    return render_template(
        'configure/lookup.html',
        entity=entity,
        results=results)

# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------

def handle_deletion(entity):
    """Safely removes an entity and triggers cascade cleanup."""
    if entity:
        if entity.id == GENERAL_ID:
            from flask import flash
            flash("Cannot delete the General Storage entity.")
            return False
        db.session.delete(entity)
        db.session.commit()
        return True
    return False

def duplicate_entity(source_id, entity_type):
    new_obj = clone_entity(source_id, entity_type)
    if new_obj:
        db.session.commit()
        return redirect(url_for(
            f'configure.edit_{entity_type}', id=new_obj.id))
    return render_template(
        'error.html',
        message="Duplication Failed",
        details=f"Unable to create a copy of {entity_type} (ID: {source_id})."
    ), HTTPStatus.BAD_REQUEST

