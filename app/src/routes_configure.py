import os
import logging
import json
import tempfile
from flask import (
    Blueprint, request, session, redirect, url_for, render_template, g,
    send_file, jsonify, current_app)
from app.models import (
    db, Entity, Item, Character, Location, Attrib, Event, 
    Recipe, RecipeSource, RecipeByproduct, AttribValue, 
    LocationDest, LocationItemRef, Overall, WinRequirement,
    Pile, GENERAL_ID)
from app.serialization import (
    export_to_dict, import_from_dict, clear_game_data, init_game_session)
from app.utils import (
    LinkLetters, parse_coords, parse_dimensions, condense_json,
    parse_form_data)

logger = logging.getLogger(__name__)
configure_bp = Blueprint('configure', __name__, url_prefix='/configure')

# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------

def get_next_id():
    """
    Thread-safe, per-token ID generation.
    Increments the counter in the 'Overall' table and returns the new ID.
    """
    # Find the Overall record for THIS token and LOCK it for this transaction
    overall = Overall.query.filter_by(game_token=g.game_token).with_for_update().first()
    assigned_id = overall.next_entity_id
    overall.next_entity_id += 1
    db.session.commit()
    
    return assigned_id

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

# ------------------------------------------------------------------------
# Main Index
# ------------------------------------------------------------------------

@configure_bp.route('/')
def index():
    """Lists all entities grouped by type."""
    items = Item.query.filter_by(game_token=g.game_token).order_by(Item.name).all()
    chars = Character.query.filter_by(game_token=g.game_token).order_by(Character.name).all()
    locs = Location.query.filter_by(game_token=g.game_token).order_by(Location.name).all()
    attribs = Attrib.query.filter_by(game_token=g.game_token).order_by(Attrib.name).all()
    events = Event.query.filter_by(game_token=g.game_token).order_by(Event.name).all()
    
    overall = Overall.query.get(g.game_token)
    
    return render_template(
        'configure/index.html',
        items=items,
        characters=chars,
        locations=locs, 
        attribs=attribs,
        events=events,
        overall=overall
    )

# ------------------------------------------------------------------------
# Item Settings
# ------------------------------------------------------------------------

@configure_bp.route('/item/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/item/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_item(id):
    game_token = g.game_token
    item = Item.query.get((game_token, id)) if id else None

    if request.method == 'POST':
        if 'delete' in request.form:
            db.session.delete(item)
            db.session.commit()
            return redirect(url_for('configure.index'))

        is_new = item is None
        if is_new:
            item = Item(game_token=game_token, id=get_next_id())
            db.session.add(item)

        # Update base Entity fields via JTI
        item.name = request.form.get('name')
        item.description = request.form.get('description')
        
        # Update Item specific fields
        item.storage_type = request.form.get('storage_type', 'universal')
        item.q_limit = float(request.form.get('q_limit', 0))
        item.toplevel = 'toplevel' in request.form
        
        # Handle General Storage Quantity (Shared ID logic)
        if item.storage_type == 'universal':
            qty = float(request.form.get('quantity', 0))
            gen_pile = Pile.query.filter_by(
                game_token=game_token, item_id=item.id, owner_id=GENERAL_ID
            ).first()
            if not gen_pile:
                gen_pile = Pile(
                    game_token=game_token, item_id=item.id, 
                    owner_id=GENERAL_ID, position=[0,0]
                )
                db.session.add(gen_pile)
            gen_pile.quantity = qty

        db.session.commit()

        if 'duplicate' in request.form:
            return duplicate_entity(item.id, 'item')
            
        return redirect(url_for('configure.index'))

    # GET: Prepare variables for the template
    gen_qty = 0
    if item:
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

# ------------------------------------------------------------------------
# Attribute Settings
# ------------------------------------------------------------------------

@configure_bp.route('/attrib/<id>', methods=['GET', 'POST'])
def edit_attrib(id):
    game_token = g.game_token
    attrib = Attrib.query.get(game_token, (int(id))) if id != 'new' else None

    if request.method == 'POST':
        if 'delete' in request.form:
            handle_deletion(attrib)
            return redirect(url_for('configure.index'))

        if not attrib:
            attrib = Attrib(game_token=game_token, id=get_next_id())
            db.session.add(attrib)

        attrib.name = request.form.get('name')
        attrib.description = request.form.get('description')
        
        v_type = request.form.get('value_type')

        attrib.is_binary = False
        attrib.enum_list = None
        
        if v_type == 'binary':
            attrib.is_binary = True
        elif v_type == 'enum':
            lines = request.form.get('enum_values', '').splitlines()
            attrib.enum_list = [l.strip() for l in lines if l.strip()]
            if not attrib.enum_list:
                # revert to a standard number
                attrib.enum_list = None 

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(attrib.id, 'attrib')
        return redirect(url_for('configure.index'))

    return render_template('configure/attrib.html', attrib=attrib)

# ------------------------------------------------------------------------
# Character Settings
# ------------------------------------------------------------------------

@configure_bp.route('/character/<id>', methods=['GET', 'POST'])
def edit_character(id):
    game_token = g.game_token
    char = Character.query.get(game_token, (int(id))) if id != 'new' else None

    if request.method == 'POST':
        if 'delete' in request.form:
            handle_deletion(char)
            return redirect(url_for('configure.index'))

        if not char:
            char = Character(game_token=game_token, id=get_next_id())
            db.session.add(char)

        data = parse_form_data(request.form)
        char.name = data.get('name')
        char.description = data.get('description')
        char.location_id = data.get('location_id', type=int) or None
        char.position = parse_coords(data.get('pos_str'))
        char.travel_group = data.get('travel_group')
        char.toplevel = 'toplevel' in data
        char.masked = 'masked' in data

        # Update Attrib values
        AttribValue.query.filter_by(game_token=game_token, subject_id=char.id).delete()
        for attrib_row in data.get('stats', []):
            attr_id = attrib_row.get('attrib_id')
            if attr_id:
                val = float(attrib_row.get('value', 0))
                db.session.add(
                    AttribValue(
                        game_token=game_token,
                        subject_id=char.id,
                        attrib_id=attr_id,
                        value=val))

        # Pile Handling
        Pile.query.filter_by(game_token=game_token, owner_id=char.id).delete()
        for item_row in data.get('items', []):
            item_id = item_row.get('item_id')
            if item_id:
                db.session.add(Pile(
                    game_token=game_token,
                    owner_id=char.id,
                    item_id=int(item_id),
                    quantity=float(item_row.get('quantity', 0)),
                    slot=item_row.get('slot'),
                    position=[0,0]
                ))

        db.session.commit()
        return redirect(url_for('configure.index'))

    return render_template('configure/character.html', 
        character=char, 
        all_locations=Location.query.filter_by(game_token=game_token).all(),
        all_attribs=Attrib.query.filter_by(game_token=game_token).all(),
        all_items=Item.query.filter_by(game_token=game_token).all(),
        overall=Overall.query.get(game_token)
    )

# ------------------------------------------------------------------------
# Location Settings
# ------------------------------------------------------------------------

@configure_bp.route('/location/<id>', methods=['GET', 'POST'])
def edit_location(id):
    game_token = g.game_token
    loc_id = int(id) if id != 'new' else None
    loc = Location.query.get((game_token, loc_id)) if loc_id else None

    if request.method == 'POST':
        data = parse_form_data(request.form)
        if 'delete' in data:
            handle_deletion(loc)
            return redirect(url_for('configure.index'))

        if not loc:
            loc = Location(game_token=game_token, id=get_next_id())
            db.session.add(loc)

        loc.name = data.get('name')
        loc.description = data.get('description')
        loc.dimensions = parse_dimensions(data.get('dimensions_str'))
        loc.toplevel = 'toplevel' in data
        loc.masked = 'masked' in data

        # Parse L,T,R,B.
        raw_excluded = parse_coords(data.get('excluded_str'))
        loc.excluded = raw_excluded[:4] if len(raw_excluded) >= 4 else None

        # Update Destinations (Exits)
        LocationDest.query.filter_by(game_token=game_token, loc1_id=loc.id).delete()
        for d in data.get('dests', []):
            target_id = d.get('target_id')
            if target_id:
                new_dest = LocationDest(
                    game_token=game_token,
                    loc1_id=loc.id,
                    loc2_id=int(target_id),
                    duration=int(d.get('duration', 1)),
                    door1=parse_coords(d.get('door_here')),
                    door2=parse_coords(d.get('door_there')),
                    bidirectional=(d.get('bidirectional') is not None)
                )
                db.session.add(new_dest)

        # Update Items on Ground
        Pile.query.filter_by(game_token=game_token, owner_id=loc.id).delete()
        for i_row in data.get('items', []):
            item_id = i_row.get('item_id')
            if item_id:
                db.session.add(Pile(
                    game_token=game_token,
                    owner_id=loc.id,
                    item_id=int(item_id),
                    quantity=float(i_row.get('quantity', 0)),
                    # Grid items need their specific coordinates
                    position=parse_coords(i_row.get('pos')) or [1, 1]
                ))

        # Update Item Refs (Universal items visible here)
        LocationItemRef.query.filter_by(game_token=game_token, loc_id=loc.id).delete()
        for r_id in data.getlist('item_refs[]'):
            db.session.add(LocationItemRef(game_token=game_token, loc_id=loc.id, item_id=int(r_id)))

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(loc.id, 'location')
        return redirect(url_for('configure.index'))

    return render_template('configure/location.html', 
        location=loc,
        destinations=LocationDest.query.filter_by(game_token=game_token, loc1_id=id).all() if id != 'new' else [],
        inventory=Pile.query.filter_by(game_token=game_token, owner_id=id).all() if id != 'new' else [],
        all_locations=Location.query.filter_by(game_token=game_token).all(),
        all_items=Item.query.filter_by(game_token=game_token).all(),
        universal_items=Item.query.filter_by(game_token=game_token, storage_type='universal').all()
    )

# ------------------------------------------------------------------------
# Event Settings
# ------------------------------------------------------------------------

@configure_bp.route('/event/<id>', methods=['GET', 'POST'])
def edit_event(id):
    game_token = g.game_token
    event = Event.query.get(game_token, (int(id))) if id != 'new' else None

    if request.method == 'POST':
        if 'delete' in request.form:
            handle_deletion(event)
            return redirect(url_for('configure.index'))

        if not event:
            event = Event(game_token=game_token, id=get_next_id())
            db.session.add(event)

        event.name = request.form.get('name')
        event.description = request.form.get('description')
        event.outcome_type = request.form.get('outcome_type')
        event.trigger_chance = request.form.get('trigger_chance', type=float)
        event.single_number = request.form.get('single_number', type=float)
        event.selection_strings = request.form.get('selection_strings')
        event.numeric_range = [request.form.get('range_min', type=int), request.form.get('range_max', type=int)]
        event.toplevel = 'toplevel' in request.form

        db.session.commit()
        if 'duplicate' in request.form:
            return duplicate_entity(event.id, 'event')
        return redirect(url_for('configure.index'))

    return render_template('configure/event.html', 
        event=event,
        all_attribs=Attrib.query.filter_by(game_token=game_token).all()
    )

# ------------------------------------------------------------------------
# Duplication Engine
# ------------------------------------------------------------------------

# team_progress/app/src/routes_configure.py

def duplicate_entity(source_id, entity_type):
    """
    Deep copies any game entity and its related records (Recipes, Stats, etc.)
    within the shared ID space.
    """
    game_token = g.game_token
    source_base = Entity.query.get((game_token, source_id))
    if not source_base:
        return redirect(url_for('configure.index'))

    new_id = get_next_id()
    new_name = increment_name(source_base.name)

    # 1. Subclass-specific Logic
    if entity_type == 'item':
        src = Item.query.get((game_token, source_id))
        new_obj = Item(
            game_token=game_token, id=new_id,
            name=new_name, description=src.description, toplevel=src.toplevel,
            masked=src.masked, storage_type=src.storage_type, q_limit=src.q_limit
        )
        db.session.add(new_obj)
        db.session.flush() # Secure the new_id

        # Deep Copy Recipes
        for r in Recipe.query.filter_by(game_token=game_token, product_id=source_id).all():
            new_recipe = Recipe(game_token=game_token, product_id=new_id, 
                                rate_amount=r.rate_amount, rate_duration=r.rate_duration, 
                                instant=r.instant)
            db.session.add(new_recipe)
            db.session.flush()
            
            for s in r.sources:
                db.session.add(RecipeSource(game_token=game_token, recipe_id=new_recipe.id, 
                                           item_id=s.item_id, q_required=s.q_required, preserve=s.preserve))
            for b in r.byproducts:
                db.session.add(RecipeByproduct(game_token=game_token, recipe_id=new_recipe.id, 
                                              item_id=b.item_id, rate_amount=b.rate_amount))

    elif entity_type == 'character':
        src = Character.query.get((game_token, source_id))
        new_obj = Character(
            game_token=game_token, id=new_id,
            name=new_name, description=src.description, toplevel=src.toplevel,
            masked=src.masked, travel_group=src.travel_group,
            location_id=src.location_id, position=src.position
        )
        db.session.add(new_obj)

    elif entity_type == 'location':
        src = Location.query.get((game_token, source_id))
        new_obj = Location(
            game_token=game_token, id=new_id,
            name=new_name, description=src.description, toplevel=src.toplevel,
            masked=src.masked, dimensions=src.dimensions, excluded=src.excluded
        )
        db.session.add(new_obj)
        db.session.flush()
        
        # Copy Universal Item References (what items are "visible" here)
        for ref in LocationItemRef.query.filter_by(game_token=game_token, loc_id=source_id).all():
            db.session.add(LocationItemRef(game_token=game_token, loc_id=new_id, item_id=ref.item_id))

    elif entity_type == 'attrib':
        src = Attrib.query.get((game_token, source_id))
        new_obj = Attrib(
            game_token=game_token, id=new_id,
            name=new_name, description=src.description,
            enum_list=src.enum_list, is_binary=src.is_binary
        )
        db.session.add(new_obj)

    elif entity_type == 'event':
        src = Event.query.get((game_token, source_id))
        new_obj = Event(
            game_token=game_token, id=new_id,
            name=new_name,
            description=src.description, toplevel=src.toplevel,
            outcome_type=src.outcome_type, trigger_chance=src.trigger_chance,
            numeric_range=src.numeric_range, single_number=src.single_number,
            selection_strings=src.selection_strings
        )
        db.session.add(new_obj)

    # 2. Shared Associations: Copy Attributes (Stats/Prerequisites)
    # This applies to almost all entity types
    attrib_values = AttribValue.query.filter_by(
        game_token=game_token, subject_id=source_id).all()
    for av in attrib_values:
        db.session.add(AttribValue(
            game_token=game_token, attrib_id=av.attrib_id,
            subject_id=new_id, value=av.value
        ))

    db.session.commit()
    return redirect(url_for(f'configure.edit_{entity_type}', id=new_id))

# ------------------------------------------------------------------------
# File Management (Save/Load)
# ------------------------------------------------------------------------

@configure_bp.route('/save')
def save_to_file():
    """Exports the current game token state to a JSON file."""
    json_data = export_game_to_json(g.game_token)
    
    overall = Overall.query.get(g.game_token)
    filename = f"{overall.title if overall else 'scenario'}.json"

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
        tmp.write(json_data)
        path = tmp.name
        
    return send_file(path, as_attachment=True, download_name=filename)

@configure_bp.route('/load', methods=['POST'])
def load_from_file():
    """Processes an uploaded JSON and updates the DB."""
    if 'file' not in request.files:
        return "No file uploaded", 400
        
    file = request.files['file']
    data = json.load(file)
    
    try:
        import_from_dict(data)
        return redirect(url_for('configure.index'))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Import failed: {e}")
        return f"Error importing scenario: {str(e)}", 500

@configure_bp.route('/clear-all')
def clear_all():
    game_token = g.game_token
    clear_game_data(game_token)
    
    # Optional: Look for a default file in the data directory
    default_path = os.path.join(current_app.config['DATA_DIR'], '00_Default.json')
    
    if os.path.exists(default_path):
        with open(default_path, 'r') as f:
            data = json.load(f)
            import_from_dict(data)
    else:
        # Fallback to minimal bootstrap if file is missing
        init_game_session(game_token)
        
    return redirect(url_for('configure.index'))

# ------------------------------------------------------------------------
# Overall Settings
# ------------------------------------------------------------------------

@configure_bp.route('/overall', methods=['GET', 'POST'])
def edit_overall():
    overall = Overall.query.get(g.game_token)
    if request.method == 'POST':
        overall.title = request.form.get('title')
        overall.description = request.form.get('description')
        overall.number_format = request.form.get('number_format', 'en_US')
        # Parse multi-line slots
        slots_text = request.form.get('slots', '')
        overall.slots = [s.strip() for s in slots_text.split('\n') if s.strip()]
        
        db.session.commit()
        return redirect(url_for('configure.index'))
        
    return render_template('configure/overall.html', overall=overall)

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
        values = AttribValue.query.filter_by(game_token=game_token, attrib_id=id).all()
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
        dests = LocationDest.query.filter(
            LocationDest.game_token == game_token,
            (LocationDest.loc1_id == id) | (LocationDest.loc2_id == id)
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

@configure_bp.route('/scenarios', methods=['GET', 'POST'])
def browse_scenarios():
    data_dir = current_app.config['DATA_DIR']
    
    if request.method == 'POST':
        filename = request.form.get('scenario_file')
        filepath = os.path.join(data_dir, filename)
        
        with open(filepath, 'r', encoding='utf-8') as f:
            scenario_data = json.load(f)
            
        # Our serialization logic handles the wipe and the bootstrap
        import_from_dict(scenario_data)
        return redirect(url_for('play.overview'))

    # GET logic: List files
    scenarios = []
    sort_by = request.args.get('sort_by', 'title')

    for filename in os.listdir(data_dir):
        if filename.endswith('.json'):
            path = os.path.join(data_dir, filename)
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    overall = data.get('overall', {})
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
