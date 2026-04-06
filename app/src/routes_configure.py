import os
import logging
import json
import tempfile
from flask import (
    Blueprint, request, session, flash, redirect, url_for, render_template,
    g, send_file, jsonify, current_app)
from app.models import (
    GENERAL_ID, StorageType, JsonKeys, ENTITIES, db,
    Entity, Item, Character, Location, Attrib, Event, 
    Pile, AttribVal, Operation, EntityAbility,
    Recipe, RecipeSource, RecipeByproduct,
    LocDest, ItemRef,
    Overall, WinRequirement)
from app.serialization import (
    init_game_session, load_scenario_from_path, DEFAULT_SCENARIO_FILE,
    import_from_dict, patch_from_dict,
    clear_game_data, export_game_to_json, export_to_dict)
from app.database import clone_with_children
from app.utils import (
    LinkLetters, RequestHelper, parse_coords,
    capture_origin, redirect_back)

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
        name: model.query.filter_by(game_token=g.game_token).order_by(model.name).all()
        for name, model in ENTITIES.items()
    }
    overall = Overall.query.get(g.game_token)
    
    return render_template(
        'configure/index.html',
        overall=overall,
        **entities
    )

# ------------------------------------------------------------------------
# Overall Settings
# ------------------------------------------------------------------------

@configure_bp.route('/overall', methods=['GET', 'POST'])
def edit_overall():
    overall = Overall.query.get(g.game_token)
    if request.method == 'POST':
        req = RequestHelper('form')
        overall.title = req.get_str('title', overall.title)
        overall.description = req.get_str('description')
        overall.number_format = req.get_str('number_format', overall.number_format)
        slots_text = req.get_str('slots')
        overall.slots = [s.strip() for s in slots_text.split('\n') if s.strip()]
        
        db.session.commit()
        return redirect(url_for('configure.index'))
        
    return render_template('configure/overall.html', overall=overall)

@configure_bp.route('/cancel')
def cancel():
    """Generic cancel route to return to the last known origin."""
    return redirect_back('configure.index')

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
            return redirect(url_for('configure.index'))

        if item.id is None:
            item.id = Overall.generate_next_id(g.game_token)
            db.session.add(item)
        
        item.name = req.get_str('name', item.name)
        item.description = req.get_str('description')
        
        val = req.get_str('storage_type')
        item.storage_type = (
            val if val in StorageType.ALL_CODES else StorageType.UNIVERSAL)
        item.q_limit = req.get_float('q_limit')
        item.toplevel = 'toplevel' in request.form
        item.masked = 'masked' in request.form
        
        if item.storage_type == StorageType.UNIVERSAL:
            qty = req.get_float('quantity')
            gen_pile = Pile.query.filter_by(
                game_token=game_token, item_id=item.id, owner_id=GENERAL_ID
            ).first()
            if qty > 0:
                if not gen_pile:
                    gen_pile = Pile(
                        game_token=game_token, item_id=item.id, 
                        owner_id=GENERAL_ID, position=None
                    )
                    db.session.add(gen_pile)
                gen_pile.quantity = qty
            elif gen_pile:
                db.session.delete(gen_pile)

        db.session.commit()

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

    return render_template('configure/item.html', 
        item=item, 
        initial_qty=gen_qty,
        all_items=Item.query.filter_by(game_token=game_token).all(),
        all_attribs=Attrib.query.filter_by(game_token=game_token).all(),
        recipes=item.recipes if item else []
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
            return redirect(url_for('configure.index'))

        if loc.id is None:
            loc.id = Overall.generate_next_id(g.game_token)
            db.session.add(loc)

        loc.name = req.get_str('name', loc.name)
        loc.description = req.get_str('description')
        loc.dimensions = parse_coords(req.get_str('dimensions_str'))
        loc.toplevel = 'toplevel' in request.form
        loc.masked = 'masked' in request.form

        lt_coords = parse_coords(req.get_str('excluded_left_top'))
        rb_coords = parse_coords(req.get_str('excluded_right_bottom'))
        if lt_coords and rb_coords:
            loc.excluded = lt_coords + rb_coords
        else:
            loc.excluded = None

        # Destinations (Exits) -- Symmetric Route Saving
        # 1. Fetch existing routes involving this location to determine what to delete/update
        existing_routes = LocDest.query.filter(
            (LocDest.game_token == game_token) & 
            ((LocDest.loc1_id == loc.id) | (LocDest.loc2_id == loc.id))
        ).all()
        existing_map = {r.id: r for r in existing_routes}
        
        submitted_ids = []
        new_coords = set()
        for row in req.get_list('dests'):
            target_id = int(row.get('target_id') or 0)
            if not target_id: continue
            route_id = int(row.get('id') or 0)
            direction = row.get('direction', 'two-way')
            
            existing_route = existing_map.get(route_id)
            old_there_door = None
            if existing_route:
                route = existing_route
                old_there_door = route.door_at(target_id)
            else:
                route = LocDest(game_token=game_token)
                db.session.add(route)

            if direction == 'backward':
                route.loc1_id = target_id
                route.loc2_id = loc.id
                route.bidirectional = False
                route.door1 = old_there_door
                route.door2 = parse_coords(row.get('door_here'))
            else:
                route.loc1_id = loc.id
                route.loc2_id = target_id
                route.bidirectional = (direction == 'two-way')
                route.door1 = parse_coords(row.get('door_here'))
                route.door2 = old_there_door

            route.duration = int(row.get('duration', 1))
            db.session.flush()

            d1_tuple = tuple(route.door1) if route.door1 else None
            if d1_tuple and d1_tuple in new_coords:
                route.door1 = None
                target_loc = Location.query.get((game_token, target_id))
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
        Pile.query.filter_by(game_token=game_token, owner_id=loc.id).delete()
        for row in req.get_list('items'):
            item_id = row.get('item_id')
            if item_id:
                db.session.add(Pile(
                    game_token=game_token,
                    owner_id=loc.id,
                    item_id=int(item_id),
                    quantity=float(row.get('quantity', 0)),
                    position=parse_coords(row.get('pos'))
                ))

        # Item Refs
        ItemRef.query.filter_by(game_token=game_token, loc_id=loc.id).delete()
        for ref_id in request.form.getlist('item_refs[]'):
            if ref_id:
                db.session.add(ItemRef(
                    game_token=game_token, 
                    loc_id=loc.id, 
                    item_id=int(ref_id)
                ))

        # Local Events
        EntityAbility.query.filter_by(game_token=game_token, entity_id=loc.id).delete()
        for row in req.get_list('events'):
            event_id = row.get('id')
            if event_id:
                db.session.add(EntityAbility(
                    game_token=game_token,
                    entity_id=loc.id,
                    event_id=int(event_id)
                ))

        # Attribute Values
        AttribVal.query.filter_by(game_token=game_token, subject_id=loc.id).delete()
        for row in req.get_list('attribs'):
            attr_id = row.get('id')
            if attr_id:
                db.session.add(AttribVal(
                    game_token=game_token,
                    subject_id=loc.id,
                    attrib_id=int(attr_id),
                    value=row.get('value', '0')
                ))

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(loc.id, 'location')
        return redirect_back('configure.index') 

    all_items = Item.query.filter_by(game_token=game_token).all()
    universal_items = [
        i for i in all_items if i.storage_type == StorageType.UNIVERSAL]
    containable_items = [
        i for i in all_items if i.storage_type != StorageType.UNIVERSAL]

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
            'duration': r.duration,
            'direction': direction
        })

    return render_template('configure/location.html', 
        location=loc,
        destinations=normalized_dests,
        inventory=Pile.query.filter_by(
            game_token=game_token, owner_id=id).all() if id != 'new' else [],
        all_locs=Location.query.filter_by(game_token=game_token).all(),
        all_attribs=Attrib.query.filter_by(game_token=game_token).all(),
        all_events=Event.query.filter_by(game_token=game_token).all(),
        all_containable_items=containable_items,
        all_universal_items=universal_items,
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
            return redirect(url_for('configure.index'))

        if char.id is None:
            char.id = Overall.generate_next_id(g.game_token)
            db.session.add(char)

        char.name = req.get_str('name', char.name)
        char.description = req.get_str('description')
        char.location_id = req.get_int('location_id', None)
        char.position = parse_coords(req.get_str('pos_str'))
        char.travel_party = req.get_str('travel_party')
        char.toplevel = 'toplevel' in request.form

        # Update Attrib values
        AttribVal.query.filter_by(game_token=game_token, subject_id=char.id).delete()
        for attrib_row in req.get_list('attribs'):
            attr_id = attrib_row.get('id')
            if attr_id:
                val = float(attrib_row.get('value', 0))
                db.session.add(
                    AttribVal(
                        game_token=game_token,
                        subject_id=char.id,
                        attrib_id=attr_id,
                        value=val))

        # Pile Handling
        Pile.query.filter_by(game_token=game_token, owner_id=char.id).delete()
        for item_row in req.get_list('items'):
            item_id = item_row.get('item_id')
            if item_id:
                db.session.add(Pile(
                    game_token=game_token,
                    owner_id=char.id,
                    item_id=int(item_id),
                    quantity=float(item_row.get('quantity', 0)),
                    slot=item_row.get('slot'),
                ))

        # Abilities
        EntityAbility.query.filter_by(game_token=game_token, entity_id=char.id).delete()
        for ability_row in req.get_list('abilities'):
            event_id = ability_row.get('id')
            if event_id:
                db.session.add(
                    EntityAbility(
                        game_token=game_token,
                        entity_id=char.id,
                        event_id=event_id
                    )
                )

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(loc.id, 'location')
        return redirect_back('configure.index') 

    return render_template('configure/character.html', 
        character=char, 
        all_locs=Location.query.filter_by(game_token=game_token).all(),
        all_attribs=Attrib.query.filter_by(game_token=game_token).all(),
        all_items=Item.query.filter_by(game_token=game_token).all(),
        all_events=Event.query.filter_by(game_token=game_token).all(),
        overall=Overall.query.get(game_token)
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
            return redirect(url_for('configure.index'))

        if attrib.id is None:
            attrib.id = Overall.generate_next_id(g.game_token)
            db.session.add(attrib)

        attrib.name = req.get_str('name', attrib.name)
        attrib.description = req.get_str('description')
        
        v_type = req.get_str('value_type')
        attrib.is_binary = False
        attrib.enum_list = None
        if v_type == 'binary':
            attrib.is_binary = True
        elif v_type == 'enum':
            lines = req.get_str('enum_values').splitlines()
            attrib.enum_list = [l.strip() for l in lines if l.strip()]
            if not attrib.enum_list:
                attrib.enum_list = None 

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
            return redirect(url_for('configure.index'))

        if event.id is None:
            event.id = Overall.generate_next_id(g.game_token)
            db.session.add(event)

        event.name = req.get_str('name', event.name)
        event.description = req.get_str('description')
        event.outcome_type = req.get_str('outcome_type')
        event.roller_type = req.get_str('roller_type')
        event.trigger_chance = req.get_float('trigger_chance')
        event.single_number = req.get_float('single_number')
        event.selection_strings = req.get_str('selection_strings')
        event.numeric_range = [
            req.get_int('range_min'), req.get_int('range_max')]
        event.toplevel = 'toplevel' in request.form

        # Auto-collapse Tertiary to Secondary for a cleaner UI
        if any(d.source_who == '3rd' for d in event.determinants) and \
           not any(d.source_who == '2nd' for d in event.determinants):
            for d in event.determinants:
                if d.source_who == '3rd':
                    d.source_who = '2nd'

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(event.id, 'event')
        return redirect_back('configure.index') 

    return render_template('configure/event.html', 
        event=event,
        all_attribs=Attrib.query.filter_by(game_token=game_token).all(),
        operations=Operation
    )

# ------------------------------------------------------------------------
# Lookup
# ------------------------------------------------------------------------

@configure_bp.route('/lookup/<string:ent_type>/<int:id>')
def lookup_entity(ent_type, id):
    game_token = g.game_token
    entity = Entity.query.get_or_404((game_token, id))
    
    # Results is a dict of lists: { 'Category Name': [ {label, name, link, meta}, ... ] }
    results = {}

    # 1. Check Pile (Who has this item / Where is this item?)
    if ent_type == 'item':
        piles = Pile.query.filter_by(game_token=game_token, item_id=id).all()
        results['Physical Presence'] = []
        for p in piles:
            owner = Entity.query.get((game_token, p.owner_id))
            label = "General Storage" if owner.id == GENERAL_ID else f"Stored at ({owner.entity_type})"
            results['Physical Presence'].append({
                'label': label,
                'name': owner.name,
                'link': url_for(f'play.play_{owner.entity_type}', id=owner.id),
                'meta': f'Qty: {p.quantity}'
            })

    # 2. Check Attributes (Who uses this attribute?)
    if ent_type == 'attrib':
        values = AttribVal.query.filter_by(game_token=game_token, attrib_id=id).all()
        results['Applied to Entities'] = []
        for v in values:
            owner = Entity.query.get((game_token, v.owner_id))
            results['Applied to Entities'].append({
                'label': f'Stat on {owner.entity_type}',
                'name': owner.name,
                'link': url_for(f'play.play_{owner.entity_type}', id=owner.id),
                'meta': f'Value: {v.value}'
            })

    # 3. Check Recipe Dependencies (Recursive analysis)
    if ent_type == 'item':
        # As an ingredient
        sources = RecipeSource.query.filter_by(game_token=game_token, item_id=id).all()
        results['Used as Ingredient'] = []
        for s in sources:
            recipe = Recipe.query.get((game_token, s.recipe_id))
            prod = Item.query.get((game_token, recipe.item_id))
            results['Used as Ingredient'].append({
                'label': 'Required to produce',
                'name': prod.name,
                'link': url_for('play.play_item', id=prod.id),
                'meta': f'Needs {s.q_required}'
            })

    # 4. Check Navigation (What links to this location?)
    if ent_type == 'location':
        dests = LocDest.query.filter(
            LocDest.game_token == game_token,
            (LocDest.loc1_id == id) | (LocDest.loc2_id == id)
        ).all()
        results['Connected Locations'] = []
        for d in dests:
            other_id = d.loc2_id if d.loc1_id == id else d.loc1_id
            other = Location.query.get((game_token, other_id))
            results['Connected Locations'].append({
                'label': 'Linked to',
                'name': other.name,
                'link': url_for('play.play_location', id=other.id),
                'meta': f'{d.duration}s travel'
            })

    return render_template('configure/lookup.html', entity=entity, results=results)

# ------------------------------------------------------------------------
# File Handling
# ------------------------------------------------------------------------

@configure_bp.route('/scenarios', methods=['GET', 'POST'])
def browse_scenarios():
    data_dir = current_app.config['DATA_DIR']
    
    if request.method == 'POST':
        req = RequestHelper('form')
        filename = req.get_str('scenario_file')
        if load_scenario_from_path(filename):
            return redirect(url_for('play.overview'))
        return "Error loading scenario", 500

    # GET logic: List files
    scenarios = []
    sort_by = request.args.get('sort_by', 'title')

    for filename in os.listdir(data_dir):
        if filename == DEFAULT_SCENARIO_FILE:
            continue
        if filename.endswith('.json'):
            path = os.path.join(data_dir, filename)
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    overall = data.get(JsonKeys.OVERALL, {})
                    scenarios.append({
                        'filename': filename,
                        'title': overall.get('title', filename),
                        'description': overall.get('description', ''),
                        'progress_type': overall.get('progress_type', 'Idle'),
                        'multiplayer': overall.get('multiplayer', False),
                        'filesize': os.path.getsize(path)
                    })
                except Exception as e:
                    logger.error(f"Error parsing {filename}: {e}")

    # Sorting
    scenarios = sorted(scenarios, key=lambda x: x.get(sort_by, ''))
    
    return render_template(
        'configure/scenarios.html', 
        scenarios=scenarios, 
        sort_by=sort_by,
        link_letters=LinkLetters(excluded='om') # Reserve 'o' and 'm' for nav
    )

@configure_bp.route('/save')
def save_to_file():
    """Exports the current game token state to a JSON file."""
    json_data = export_game_to_json()
    
    overall = Overall.query.get(g.game_token)
    title = (overall.title or '').strip() or 'scenario'
    filename = f"{title}.json"

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
        tmp.write(json_data)
        path = tmp.name
        
    return send_file(path, as_attachment=True, download_name=filename)

@configure_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    """Processes an uploaded JSON and updates the DB."""
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file uploaded", 400
        file = request.files['file']
        json_data = json.load(file)
        try:
            mode = request.form.get('active_mode')
            if mode == 'patch':
                patch_from_dict(json_data)
            else:
                import_from_dict(json_data)
            return redirect(url_for('play.overview'))
        except (SyntaxError, NameError, AttributeError):
            raise
        except Exception as ex:
            db.session.rollback()
            return render_template(
                'error.html',
                message="Couldn't Import",
                details=str(ex))

    return render_template(
        'configure/upload.html', 
    )

@configure_bp.route('/clear-all', methods=['POST'])
def clear_all():
    """Wipes data and re-applies the default scenario."""
    clear_game_data()
    init_game_session() 
    return redirect(url_for('play.overview'))

# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------

def increment_name(name):
    """Adds ' 2' or increments a trailing number for duplicates."""
    import re
    match = re.search(r'(.*?)(\d*)$', name)
    base, num = match.groups()
    if num:
        return f"{base}{int(num) + 1}"
    return f"{name} 2"

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
    """
    Deep copies any game entity and its related records (Recipes, Attrs, etc.)
    within the shared ID space.
    """
    game_token = g.game_token
    model_class = ENTITIES[f"{entity_type}s"]
    src = model_class.query.get((game_token, source_id))
    if not src:
        return redirect(url_for('configure.index'))
    
    new_id = Overall.generate_next_id(game_token)
    new_name = increment_name(src.name)

    # Recursive Clone e.g. Item -> Recipes -> Sources/Byproducts
    clone_with_children(src, {'id': new_id, 'name': new_name})
    
    db.session.commit()
    return redirect(url_for(f'configure.edit_{entity_type}', id=new_id))
