import os
import logging
import json
import tempfile
from flask import (
    Blueprint, request, session, redirect, url_for, render_template, g,
    send_file, jsonify, current_app)
from app.models import (
    GENERAL_ID, StorageType, JsonKeys, ENTITIES, db,
    Entity, Item, Character, Location, Attrib, Event, 
    Pile, Recipe, RecipeSource, RecipeByproduct, AttribValue, 
    LocationDest, ItemRef, Overall, WinRequirement)
from app.serialization import (
    init_game_session, load_scenario_from_path, import_from_dict,
    clear_game_data, export_game_to_json, export_to_dict)
from app.utils import (
    LinkLetters, RequestHelper, parse_coords, parse_dimensions,
    parse_form_data)

logger = logging.getLogger(__name__)
configure_bp = Blueprint('configure', __name__, url_prefix='/configure')

# ------------------------------------------------------------------------
# Main Index
# ------------------------------------------------------------------------

@configure_bp.route('/')
def index():
    """Lists all entities grouped by type."""
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
        item.description = req.get_str('description', item.description)
        
        storage_map = StorageType.get_lc_map()
        storage_lc = req.get_str('storage_type').lower()
        item.storage_type = storage_map.get(storage_lc, StorageType.UNIVERSAL)
        item.q_limit = req.get_float('q_limit', 0.0)
        item.toplevel = 'toplevel' in request.form
        item.masked = 'masked' in request.form
        
        if item.storage_type == 'universal':
            qty = req.get_float('quantity', 0.0)
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

@configure_bp.route('/attrib/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/attrib/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_attrib(id):
    game_token = g.game_token
    attrib = Attrib.get_or_new(game_token, id)

    if request.method == 'POST':
        if 'delete' in request.form:
            handle_deletion(attrib)
            return redirect(url_for('configure.index'))

        if attrib.id is None:
            attrib.id = Overall.generate_next_id(g.game_token)
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

@configure_bp.route('/character/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/character/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_character(id):
    game_token = g.game_token
    char = Character.get_or_new(game_token, id)

    if request.method == 'POST':
        if 'delete' in request.form:
            handle_deletion(char)
            return redirect(url_for('configure.index'))

        if char.id is None:
            char.id = Overall.generate_next_id(g.game_token)
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

@configure_bp.route('/location/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/location/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_location(id):
    game_token = g.game_token
    loc = Location.get_or_new(game_token, id)

    if request.method == 'POST':
        data = parse_form_data(request.form)
        if 'delete' in data:
            handle_deletion(loc)
            return redirect(url_for('configure.index'))

        if loc.id is None:
            loc.id = Overall.generate_next_id(g.game_token)
            db.session.add(char)

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
        ItemRef.query.filter_by(game_token=game_token, loc_id=loc.id).delete()
        for r_id in data.getlist('item_refs[]'):
            db.session.add(ItemRef(game_token=game_token, loc_id=loc.id, item_id=int(r_id)))

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

@configure_bp.route('/event/<int:id>', methods=['GET', 'POST'])
@configure_bp.route('/event/new', defaults={'id': None}, methods=['GET', 'POST'])
def edit_event(id):
    game_token = g.game_token
    event = Event.get_or_new(game_token, id)

    if request.method == 'POST':
        if 'delete' in request.form:
            handle_deletion(event)
            return redirect(url_for('configure.index'))

        if event.id is None:
            event.id = Overall.generate_next_id(g.game_token)
            db.session.add(char)

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

# ------------------------------------------------------------------------
# File Handling
# ------------------------------------------------------------------------

@configure_bp.route('/scenarios', methods=['GET', 'POST'])
def browse_scenarios():
    data_dir = current_app.config['DATA_DIR']
    
    if request.method == 'POST':
        filename = request.form.get('scenario_file')
        if load_scenario_from_path(filename):
            return redirect(url_for('play.overview'))
        return "Error loading scenario", 500

    # GET logic: List files
    scenarios = []
    sort_by = request.args.get('sort_by', 'title')

    for filename in os.listdir(data_dir):
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
        data = json.load(file)
        try:
            import_from_dict(data)
            return redirect(url_for('play.overview'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Import failed: {e}")
            return f"Error importing scenario: {str(e)}", 500

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
    Deep copies any game entity and its related records (Recipes, Stats, etc.)
    within the shared ID space.
    """
    game_token = g.game_token
    source_base = Entity.query.get((game_token, source_id))
    if not source_base:
        return redirect(url_for('configure.index'))

    new_id = Overall.generate_next_id(g.game_token)
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
        
        # Copy Item References
        for ref in ItemRef.query.filter_by(game_token=game_token, loc_id=source_id).all():
            db.session.add(ItemRef(game_token=game_token, loc_id=new_id, item_id=ref.item_id))

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
