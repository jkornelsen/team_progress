import logging
import re
from sys import maxsize

from flask import (
    g, jsonify, make_response, redirect, render_template, request, session,
    url_for)

from .attrib import Attrib
from .db_serializable import DeletionError
from .character import Character, OwnedItem
from .item import Item
from .event import Event, TriggerException, OPERATIONS, MODES
from .location import Grid, Location, ItemAt
from .overall import Overall
from .progress import Progress
from .user_interaction import MessageLog
from .utils import (
    LinkLetters, NumTup, RequestHelper, Storage, entity_class, format_num)

logger = logging.getLogger(__name__)

def set_routes(app):
    @app.route('/configure/attrib/<attrib_id>', methods=['GET', 'POST'])
    def configure_attrib(attrib_id):
        logger.debug("%s\nconfigure_attrib(%s)", "-" * 80, attrib_id)
        session['referrer_link'] = {}
        attrib = Attrib.load_complete_object(attrib_id)
        if request.method == 'GET':
            req = RequestHelper('args')
            if not req.has_key('duplicated'):
                session['referrer'] = request.referrer
            return render_template(
                'configure/attrib.html',
                current=attrib,
                game_data=g.game_data
                )
        try:
            attrib.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        req = RequestHelper('form')
        if req.has_key('make_duplicate'):
            dup_attrib = attrib
            dup_attrib.id = 0
            dup_attrib.name = increment_name(attrib.name)
            dup_attrib.to_db()  # Changes the ID
            return redirect(
                url_for('configure_attrib', attrib_id=dup_attrib.id,
                duplicated=True))
        return back_to_referrer()

    @app.route('/configure/character/<char_id>', methods=['GET', 'POST'])
    def configure_character(char_id):
        logger.debug("%s\nconfigure_character(%s)", "-" * 80, char_id)
        if request.method == 'GET':
            req = RequestHelper('args')
            if not req.has_key('duplicated'):
                session['referrer'] = request.referrer
            g.game_data.overall = Overall.load_complete_object()
            char = Character.data_for_configure(char_id)
            char.events = [
                Event.get_by_id(event_id)
                for event_id in char.events]
            return render_template(
                'configure/character.html',
                current=char,
                game_data=g.game_data
                )
        try:
            new_char = Character(char_id)
            new_char.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        req = RequestHelper('form')
        if req.has_key('make_duplicate'):
            dup_char = new_char
            dup_char.id = 0
            dup_char.name = increment_name(new_char.name)
            dup_char.progress = Progress(pholder=dup_char)
            dup_char.to_db()  # Changes the ID
            return redirect(
                url_for('configure_character', char_id=dup_char.id,
                duplicated=True))
        return back_to_referrer()

    @app.route('/configure/event/<event_id>', methods=['GET', 'POST'])
    def configure_event(event_id):
        logger.debug("%s\nconfigure_event(%s)", "-" * 80, event_id)
        session['referrer_link'] = {}
        if request.method == 'GET':
            req = RequestHelper('args')
            if not req.has_key('duplicated'):
                session['referrer'] = request.referrer
            event = Event.data_for_configure(event_id)
            return render_template(
                'configure/event.html',
                current=event,
                operations=OPERATIONS,
                modes=MODES,
                game_data=g.game_data
                )
        try:
            new_event = Event(event_id)
            new_event.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        req = RequestHelper('form')
        if req.has_key('make_duplicate'):
            dup_event = new_event
            dup_event.id = 0
            dup_event.name = increment_name(new_event.name)
            dup_event.to_db()  # Changes the ID
            return redirect(
                url_for('configure_event', event_id=dup_event.id,
                duplicated=True))
        return back_to_referrer()

    @app.route('/configure/item/<item_id>', methods=['GET', 'POST'])
    def configure_item(item_id):
        logger.debug("%s\nconfigure_item(%s)", "-" * 80, item_id)
        if request.method == 'GET':
            req = RequestHelper('args')
            if not req.has_key('duplicated'):
                session['referrer'] = request.referrer
            item = Item.data_for_configure(item_id)
            if item_id == 'new':
                item.storage_type = session.get(
                    'default_storage_type', item.storage_type)
            return render_template(
                'configure/item.html',
                current=item,
                game_data=g.game_data
                )
        try:
            new_item = Item(item_id)
            new_item.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        req = RequestHelper('form')
        if req.has_key('make_duplicate'):
            dup_item = new_item
            dup_item.id = 0
            dup_item.name = increment_name(new_item.name)
            dup_item.progress = Progress(pholder=dup_item)
            recipes = new_item.recipes
            dup_item.recipes = []
            dup_item.to_db()  # Changes the ID
            for recipe in recipes:
                recipe.id = 0
                recipe.item_produced = dup_item
                recipe.to_db()  # Changes the ID
            return redirect(
                url_for('configure_item', item_id=dup_item.id,
                duplicated=True))
        return back_to_referrer()

    @app.route('/configure/location/<loc_id>',methods=['GET', 'POST'])
    def configure_location(loc_id):
        logger.debug("%s\nconfigure_location(%s)", "-" * 80, loc_id)
        if request.method == 'GET':
            req = RequestHelper('args')
            if not req.has_key('duplicated'):
                session['referrer'] = request.referrer
            loc = Location.data_for_configure(loc_id)
            return render_template(
                'configure/location.html',
                current=loc,
                game_data=g.game_data
                )
        try:
            new_loc = Location(loc_id)
            new_loc.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        req = RequestHelper('form')
        if req.has_key('make_duplicate'):
            dup_loc = new_loc
            dup_loc.id = 0
            dup_loc.name = increment_name(new_loc.name)
            dup_loc.to_db()  # Changes the ID
            return redirect(
                url_for('configure_location', loc_id=dup_loc.id,
                duplicated=True))
        return back_to_referrer()

    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        logger.debug("%s\nconfigure_overall", "-" * 80)
        Overall.data_for_configure()
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/overall.html',
                current=g.game_data.overall,
                game_data=g.game_data
                )
        g.game_data.overall.configure_by_form()
        return redirect(url_for('configure_index'))

    @app.route('/lookup/attrib/<int:attrib_id>', methods=['GET'])
    @app.route('/lookup/character/<int:char_id>', methods=['GET'])
    @app.route('/lookup/event/<int:event_id>', methods=['GET'])
    @app.route('/lookup/item/<int:item_id>', methods=['GET'])
    @app.route('/lookup/location/<int:loc_id>',methods=['GET'])
    def lookup(**params):
        logger.debug(
            "%s\nlookup(%s)", 
            "-" * 80, 
            ', '.join(f"{key}={value}" for key, value in params.items()))
        current, uses = Overall.data_for_lookup(**params)
        return render_template(
            'configure/lookup.html',
            current=current,
            uses=uses,
            game_data=g.game_data,
            link_letters=LinkLetters('mo')
            )

    @app.route(
        '/play/attrib/<int:attrib_id>/<subject_type>/<int:subject_id>',
        methods=['GET', 'POST'])
    def play_attrib(attrib_id, subject_type, subject_id):
        logger.debug(
            "%s\nplay_attrib(attrib_id=%d, subject_type=%s, subject_id=%d)",
            "-" * 80, attrib_id, subject_type, subject_id)
        attrib = Attrib.load_complete_object(attrib_id)
        if not attrib:
            return "Attribute not found"
        produced_items = attrib.load_recipes_used_for()
        if subject_type == 'char':
            func_load_subject = Character.load_complete_object
        elif subject_type == 'item':
            func_load_subject = Item.load_complete_object
        elif subject_type == 'loc':
            func_load_subject = Location.load_complete_object
        else:
            return f"Unexpected subject type '{subject_type}'"
        subject = func_load_subject(subject_id)
        if not subject:
            return f"{subject_type} id [{subject_id}] not found"
        attrib_for = subject.attribs.get(attrib_id, 0)
        if request.method == 'POST':
            req = RequestHelper('form')
            req.debug()
            session['last_operand'] = req.get_str('operand')
            session['last_operator'] = req.get_str('operator')
            result = req.get_int('result')
            oldval = attrib_for.val
            attrib_for.val = result
            subject.to_db()
            # Reload from database
            subject = func_load_subject(subject_id)
            attrib_for = subject.attribs.get(attrib_id, 0)
            difference = attrib_for.val - oldval
            changeword = 'increased' if difference > 0 else 'changed'
            MessageLog.add(
                f"{subject.name} {attrib.name}"
                f" {changeword} by {format_num(difference)}")
        return render_template(
            'play/attrib.html',
            current=attrib,
            subject=subject,
            subject_attrib_val=attrib_for.val,
            produced_items=produced_items,
            link_letters=LinkLetters('cemo')
            )

    @app.route('/play/char/<int:char_id>')
    def play_character(char_id):
        logger.debug("%s\nplay_character(%d)", "-" * 80, char_id)
        instance = Character.data_for_play(char_id)
        if not instance:
            return "Character not found"
        travel_groups = []
        if instance.location:
            travel_groups = Character.load_travel_groups(
                instance.location.id, instance.id)
        session['last_char_id'] = char_id
        session['default_pickup_char'] = char_id
        session['referrer_link'] = {
            'url': request.url,
            'name': instance.name}
        defaults = {
            'travel_with': session.get('default_travel_with', '')}
        return render_template(
            'play/character.html',
            current=instance,
            game_data=g.game_data,
            travel_groups=travel_groups,
            defaults=defaults,
            link_letters=LinkLetters('egomstw')
            )

    @app.route('/play/event/<int:event_id>', methods=['GET', 'POST'])
    def play_event(event_id):
        req = RequestHelper('args')
        from_id = req.get_int('from_id', '')
        from_typename = req.get_str('from_typename', '')
        logger.debug(
            "%s\nplay_event(event_id=%d) from %s %s", "-" * 80,
            event_id, from_typename, from_id)
        instance = Event.data_for_configure(event_id)
        if not instance:
            return "Event not found"
        if request.method == 'GET':
            Character.load_complete_objects()
            Item.load_complete_objects()
            Location.load_complete_objects()
            from_entity = None
            if from_typename:
                from_cls = entity_class(
                    from_typename, [Character, Item, Location])
                from_entity = from_cls.get_by_id(from_id)
            return render_template(
                'play/event.html',
                current=instance,
                from_entity=from_entity,
                game_data=g.game_data,
                message=session.pop('message', ''),
                changed_by_form=session.pop('changed_by_form', False),
                operations=OPERATIONS,
                modes=MODES,
                link_letters=LinkLetters('ademor')
                )
        Event.change_by_form()
        return redirect(
            url_for('play_event', event_id=event_id))

    @app.route('/play/item/<int:item_id>/')
    def play_item(item_id):
        req = RequestHelper('args')
        char_id = req.get_int('char_id', '')
        loc_id = req.get_int('loc_id', '')
        if char_id:
            session['last_char_id'] = char_id
        if loc_id:
            session['last_loc_id'] = loc_id
        if not char_id:
            char_id = session.get('last_char_id', '')
        if not loc_id:
            loc_id = session.get('last_loc_id', '')
        pos = req.get_numtup('pos')
        if pos:
            session['last_pos'] = pos.as_list()
        main_pile_type = req.get_str('main', '')
        logger.debug(
            "%s\nplay_item(item_id=%d, char_id=%s, loc_id=%s, pos=(%s))",
            "-" * 80, item_id, char_id, loc_id, pos)
        g.game_data.overall = Overall.load_complete_object()
        item = Item.data_for_play(
            item_id, char_id, loc_id, pos, complete_sources=False,
            main_pile_type=main_pile_type)
        if not item:
            return "Item not found"
        progress = item.progress
        max_batches = {}
        for recipe in item.recipes:
            progress.set_recipe_by_id(recipe.id)
            num_batches, _ = progress.determine_batches(maxsize)
            num_batches = 1 if num_batches == maxsize else num_batches
            max_batches[recipe.id] = num_batches
        defaults = {
            'pickup_char': session.get('default_pickup_char', ''),
            'movingto_char': session.get('default_movingto_char', ''),
            'slot': session.get('default_slot', '')
            }
        params = {
            'char_id': char_id,
            'loc_id': loc_id,
            'pos': pos,
            'main_pile_type': main_pile_type
            }
        session['referrer_link'] = {
            'url': request.url,
            'name': item.name}
        characters_at_loc = []
        if isinstance(item.pile, ItemAt):
            characters_at_loc = Location.chars_at_pos(
                loc_id, item.pile.position)
        return render_template(
            'play/item.html',
            current=item,
            container=item.pile.container,
            params=params,
            defaults=defaults,
            max_batches=max_batches,
            characters_at_loc=characters_at_loc,
            game_data=g.game_data,
            link_letters=LinkLetters('delmopq')
            )

    @app.route('/play/location/<int:loc_id>')
    def play_location(loc_id):
        logger.debug("%s\nplay_location(%d)", "-" * 80, loc_id)
        instance = Location.data_for_play(loc_id)
        if not instance:
            return "Location not found"
        travel_groups = []
        if instance:
            travel_groups = Character.load_travel_groups(instance.id)
        session['last_loc_id'] = loc_id
        session['referrer_link'] = {
            'url': request.url,
            'name': instance.name}
        req = RequestHelper('args')
        char_id = req.get_int('char_id', '')
        if char_id:
            session['last_char_id'] = char_id
        else:
            char_id = session.get('last_char_id', '')
        defaults = {
            'move_char': session.get('default_move_char', ''),
            'travel_with': session.get('default_travel_with', '')}
        return render_template(
            'play/location.html',
            current=instance,
            char_id=char_id,
            defaults=defaults,
            game_data=g.game_data,
            travel_groups=travel_groups,
            link_letters=LinkLetters('emo')
            )

    @app.route('/overview')
    def overview():
        session['referrer_link'] = {
            'url': request.url,
            'name': 'Overview'}
        try:
            overview_data = Overall.data_for_overview()
        except TriggerException as ex:
            return render_template(
                'play/overview_confirm.html',
                interrupt=ex.json_data)
        response = make_response(
            render_template(
                'play/overview.html',
                current_data=overview_data,
                log_messages=MessageLog.get_recent(),
                link_letters=LinkLetters('m')
                ))
        session.pop('clear_local_storage', None)
        session.pop('ignore_event', None)
        return response

    @app.route('/char/progress/<int:char_id>', methods=['POST'])
    def char_progress(char_id):
        logger.debug("%s\nchar_progress(%d)", "-" * 80, char_id)
        char = Character.data_for_play(char_id)
        if not char:
            return jsonify({
                'status': 'error',
                'message': "Character not found."
                })
        current_loc_id = char.location.id if char.location else 0
        if not char.location or not char.dest_loc:
            return jsonify({
                'status': 'error',
                'message': 'No travel destination.',
                'current_loc_id': current_loc_id
                })
        req = RequestHelper('form')
        if not char.progress.is_ongoing:
            message = "not travelling"
            logger.debug(message)
            return jsonify({
                'status': message,
                'current_loc_id': char.location.id
                })
        elapsed_time = char.progress.calculate_elapsed_time()
        batches_done = char.progress.batches_for_elapsed_time(elapsed_time)
        try:
            Event.check_triggers(
                char.location.id, Location.typename(), char.name, batches_done,
                req)
        except TriggerException as ex:
            return jsonify(ex.json_data)
        if batches_done:
            # arrived at the destination
            main_char = char
            dest_char_pos = NumTup((0, 0))
            dest = main_char.destination
            if dest:
                door_pos = dest.other_door
                if dest.other_loc.grid.in_grid(door_pos):
                    dest_char_pos = door_pos
            for char in get_travel_chars(req, char_id):
                char.location = char.dest_loc
                char.position = dest_char_pos
                char.dest_loc = None
                char.progress.stop()
            loc = Location.from_db_flat(char.location.id)
            MessageLog.add(f"{main_char.name} arrived at {loc.name}.")
            return jsonify({
                'status': 'arrived',
                'current_loc_id': main_char.location.id
                })
        logger.debug("dest_id: %d", char.dest_loc.id)
        return jsonify({
            'status': 'ongoing',
            'is_ongoing': char.progress.is_ongoing,
            'current_loc_id': current_loc_id,
            'dest_id': char.dest_loc.id,
            'elapsed_time': elapsed_time
            })

    @app.route('/char/start/<int:char_id>', methods=['POST'])
    def start_char(char_id):
        logger.debug("%s\nstart_char(%d)", "-" * 80, char_id)
        req = RequestHelper('form')
        dest_id = req.get_int('dest_id')
        if not dest_id:
            return jsonify({
                'status': 'error',
                'message': 'No travel destination.',
                'dest_id': dest_id
                })
        session['default_travel_with'] = req.get_str('travel_with')
        for char in get_travel_chars(req, char_id):
            logger.debug("char %d", char.id)
            if not char.dest_loc or char.dest_loc.id != dest_id:
                char.dest_loc = Location(dest_id)
                char.to_db()
            if not char.progress.start():
                return jsonify({
                    'status': 'error',
                    'message': f'{char.name} could not start travel.'
                    })
        return jsonify({
            'status': 'success',
            'message': 'Progress started.',
            'is_ongoing': char.progress.is_ongoing
            })

    @app.route('/char/stop/<int:char_id>', methods=['POST'])
    def stop_char(char_id):
        logger.debug("%s\nstop_char(%d)", "-" * 80, char_id)
        paused = False
        req = RequestHelper('form')
        for char in get_travel_chars(req, char_id):
            if char.progress.stop():
                paused = True
        if paused:
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})

    @app.route('/char/go/<int:char_id>', methods=['POST'])
    def go_char(char_id):
        logger.debug("%s\ngo_char(%d)", "-" * 80, char_id)
        req = RequestHelper('form')
        dest_id = req.get_int('dest_id')
        if not dest_id:
            return jsonify({
                'status': 'error',
                'message': 'No travel destination.',
                'dest_id': dest_id
                })
        session['default_travel_with'] = req.get_str('travel_with')
        main_char = None
        loc = Location.from_db_flat(dest_id)
        travel_chars = get_travel_chars(req, char_id)
        main_char = next(
            (char for char in travel_chars
            if char.id == char_id), None)
        main_char.get_destinations(dest_id)
        dest_char_pos = NumTup((0, 0))
        dest = main_char.destination
        if dest:
            door_pos = dest.other_door
            if dest.other_loc.grid.in_grid(door_pos):
                dest_char_pos = door_pos
        for char in travel_chars:
            char.location = loc
            char.position = dest_char_pos
            char.to_db()
        if main_char is None:
            raise ValueError(f"Could not find character {char_id} to travel.")
        MessageLog.add(f"{main_char.name} went to {loc.name}.")
        return jsonify({
            'status': 'arrived',
            'current_loc_id': dest_id
            })

    @app.route('/event/roll/<int:event_id>', methods=['POST'])
    def event_roll(event_id):
        logger.debug("%s\nevent_roll(event_id=%d)", "-" * 80, event_id)
        instance = Event.data_for_configure(event_id)
        if not instance:
            return jsonify({'error': "Event not found"})
        req = RequestHelper('form')
        req.debug()
        outcome, display = instance.roll_for_outcome(
            req.get_int('die_min'),
            req.get_int('die_max'),
            req.get_int('location')
            )
        roll_counter = req.get_int('roll_counter', 0)
        return jsonify({
            'outcome': outcome,
            'outcome_display': display,
            'roll_counter': roll_counter + 1
            })

    @app.route('/item/progress/<int:item_id>/')
    def item_progress(item_id):
        req = RequestHelper('args')
        char_id = req.get_int('char_id', '')
        loc_id = req.get_int('loc_id', '')
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        main_pile_type = req.get_str('main', '')
        pos = req.get_numtup('pos')
        logger.debug(
            "%s\nitem_progress(item_id=%d, char_id=%s, loc_id=%s, pos=(%s),"
            "main_pile_type=%s)",
            "-" * 80, item_id, char_id, loc_id, pos, main_pile_type)
        main_item = Item.data_for_play(
            item_id, char_id, loc_id, pos, complete_sources=True,
            main_pile_type=main_pile_type)
        if not main_item:
            return jsonify({'error': "Item not found"})
        all_items = [main_item]
        for recipe in main_item.recipes:
            for source in recipe.sources:
                source.item.load_for_progress(
                    char_id, loc_id, pos)
                all_items.append(source.item)
        for item in all_items:
            progress = item.progress
            if progress.recipe.id and progress.is_ongoing:
                batches_done = progress.batches_for_elapsed_time()
                try:
                    Event.check_triggers(
                        item.id, Item.typename(), item.name, batches_done,
                        req)
                except TriggerException as ex:
                    return jsonify(ex.json_data)
        main_item.count_for_unmasking(char_id, loc_id, pos)
        progress = main_item.progress
        pile = main_item.pile
        logger.debug(
            "Retrieved item %d from DB: %d recipes, recipe id %d\n"
            "Pile container: %s %s",
            main_item.id, len(main_item.recipes), progress.recipe.id,
            pile.container_type(), pile.container.name)
        return jsonify({
            'main': {
                'is_ongoing': progress.is_ongoing,
                'recipe_id': progress.recipe.id,
                'quantity': pile.quantity,
                'quantity_str': format_num(pile.quantity),
                'elapsed_time': progress.calculate_elapsed_time()
                },
            'sources': [
                {
                    'id': source.item.id,
                    'quantity': format_num(source.pile.quantity)
                }
                for recipe in main_item.recipes
                for source in recipe.sources
                ],
            'unmasked_items': session.pop('unmasked_items', False),
            })

    @app.route('/item/start/<int:item_id>/<int:recipe_id>')
    def start_item(item_id, recipe_id):
        if not recipe_id:
            return jsonify({
                'status': 'error',
                'message': "No recipe.",
                })
        req = RequestHelper('args')
        char_id = req.get_int('char_id', '')
        loc_id = req.get_int('loc_id', '')
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        pos = None
        if loc_id and not char_id:
            pos = NumTup.from_list(session.get('last_pos', None))
        logger.debug(
            "%s\nstart_item(item_id=%d, recipe_id=%d, char_id=%s,"
            " loc_id=%s, pos=(%s))",
            "-" * 80, item_id, recipe_id, char_id, loc_id, pos)
        item = Item.data_for_play(
            item_id, char_id, loc_id, pos, complete_sources=True)
        progress = item.progress
        if progress.start(recipe_id):
            return jsonify({
                'status': 'success',
                'message': 'Progress started.',
                'is_ongoing': progress.is_ongoing
                })
        message = progress.failure_reason or "Could not start."
        return jsonify({
            'status': 'error',
            'message': message,
            'is_ongoing': progress.is_ongoing
            })

    @app.route('/item/stop/<int:item_id>')
    def stop_item(item_id):
        req = RequestHelper('args')
        char_id = req.get_int('char_id', '')
        loc_id = req.get_int('loc_id', '')
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        pos = None
        if loc_id and not char_id:
            pos = NumTup.from_list(session.get('last_pos', None))
        logger.debug("%s\nstop_item(%d)", "-" * 80, item_id)
        item = Item.data_for_play(
            item_id, char_id, loc_id, pos, complete_sources=True)
        logger.debug("Retrieved item %d from DB: %d recipes",
            item.id, len(item.recipes))
        container = item.pile.container
        progress = item.progress
        if progress.is_ongoing:
            progress.batches_for_elapsed_time()
            progress.stop()
            return jsonify({
                'status': 'success',
                'message': 'Progress paused.',
                'is_ongoing': progress.is_ongoing
                })
        return jsonify({
            'status': 'success',
            'message': 'Progress is already paused.',
            'is_ongoing': progress.is_ongoing
            })

    @app.route('/item/gain/<int:item_id>/<int:recipe_id>', methods=['POST'])
    def gain_item(item_id, recipe_id):
        req = RequestHelper('args')
        char_id = req.get_int('char_id', '')
        loc_id = req.get_int('loc_id', '')
        req = RequestHelper('form')
        num_batches = req.get_int('quantity', 0)
        session['quantity_to_gain'] = num_batches or 1
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        pos = None
        if loc_id and not char_id:
            pos = NumTup.from_list(session.get('last_pos', None))
        logger.debug(
            "%s\ngain_item(item_id=%d, recipe_id=%d, num_batches=%d, "
            "char_id=%s, loc_id=%s, pos=(%s))",
            "-" * 80, item_id, recipe_id, num_batches, char_id, loc_id, pos)
        item = Item.data_for_play(
            item_id, char_id, loc_id, pos, complete_sources=True)
        progress = item.progress
        progress.set_recipe_by_id(recipe_id)
        changed = progress.change_quantity(num_batches)
        if changed:
            return jsonify({
                'status': 'success',
                'message': f'Quantity of {item.name} changed.'
                })
        message = "Nothing gained."
        reason = progress.failure_reason
        if reason:
            message = f"{message} {reason}"
        return jsonify({
            'status': 'error',
            'message': message
            })

    @app.route(
        '/item/drop/<int:item_id>/char/<int:char_id>/qty/<string:qty_to_drop>',
        methods=['POST'])
    def drop_item(item_id, char_id, qty_to_drop):
        logger.debug(
            "%s\ndrop_item(item_id=%d, char_id=%d, qty=%s)",
            "-" * 80, item_id, char_id, qty_to_drop)
        try:
            qty_to_drop = float(qty_to_drop)
        except ValueError:
            return jsonify({
                'status': 'error',
                'message': 'Invalid quantity.'
                })
        item = Item.load_complete_object(item_id)
        char = Character.load_complete_object(char_id)
        owned_item = char.owned_items.get(item_id)
        if not owned_item:
            return jsonify({
                'status': 'error',
                'message': f'No item {item.name} in {char.name} inventory.'
                })
        new_qty = min(qty_to_drop, owned_item.quantity)
        loc = Location.load_complete_object(char.location.id)
        changed_item_at = None
        for item_at in loc.items_at.get(item_id, []):
            if item_at.position == char.position:
                changed_item_at = item_at
        if changed_item_at:
            new_qty += changed_item_at.quantity
        else:
            changed_item_at = ItemAt(owned_item.item, loc)
            changed_item_at.position = char.position
            changed_item_at.slot = ''
            loc.items_at.setdefault(item_id, []).append(changed_item_at)
        if item.exceeds_limit(new_qty):
            return jsonify({
                'status': 'error',
                'message': f'Limit of {item.name} is {item.q_limit}.'
                })
        changed_item_at.quantity = new_qty
        if qty_to_drop >= owned_item.quantity:
            del char.owned_items[item_id]
        else:
            owned_item.quantity -= qty_to_drop
        loc.to_db()
        char.to_db()
        return jsonify({
            'status': 'success',
            'message': MessageLog.add(f'{char.name} dropped {item.name}.')
            })

    @app.route(
        '/item/pickup/<int:item_id>/loc/<int:loc_id>'
        '/pos/<string:pos_str>/char/<int:char_id>',
        methods=['POST'])
    def pickup_item(item_id, loc_id, pos_str, char_id):
        logger.debug(
            "%s\npickup_item(item_id=%d, loc_id=%d, pos=(%s), char_id=%d)",
            "-" * 80, item_id, loc_id, pos_str, char_id)
        session['default_pickup_char'] = char_id
        item = Item.load_complete_object(item_id)
        loc = Location.load_complete_object(loc_id)
        pos = NumTup.from_str(pos_str)
        changed_item_at = None
        for index, item_at in enumerate(loc.items_at.get(item_id, [])):
            if item_at.position == pos:
                changed_item_at = item_at
                index_changed_item_at = index
        if not changed_item_at:
            return jsonify({
                'status': 'error',
                'message': f'No item {item.name} at {loc.name} pos {pos_str}.'
                })
        new_qty = changed_item_at.quantity
        char = Character.load_complete_object(char_id)
        if item_id in char.owned_items:
            owned_item = char.owned_items[item_id]
            new_qty += owned_item.quantity
        else:
            owned_item = OwnedItem(changed_item_at.item, char)
        if item.exceeds_limit(new_qty):
            return jsonify({
                'status': 'error',
                'message': f'Limit of {item.name} is {item.q_limit}.'
                })
        owned_item.quantity = new_qty
        char.owned_items[item_id] = owned_item
        del loc.items_at[item_id][index_changed_item_at]
        char.to_db()
        loc.to_db()
        return jsonify({
            'status': 'success',
            'message': MessageLog.add(f'{char.name} picked up {item.name}.')
            })

    @app.route(
        '/item/equip/<int:item_id>/char/<int:char_id>/slot/<string:slot>',
        methods=['POST'])
    def equip_item(item_id, char_id, slot):
        logger.debug(
            "%s\nequip_item(item_id=%d, char_id=%d, slot=%s)",
            "-" * 80, item_id, char_id, slot)
        session['default_slot'] = slot
        item = Item.load_complete_object(item_id)
        char = Character.load_complete_object(char_id)
        owned_item = char.owned_items.get(item_id)
        if not owned_item:
            return jsonify({
                'status': 'error',
                'message': f"No item {item.name} in {char.name}'s inventory."
                })
        owned_item.slot = slot
        char.to_db()
        return jsonify({
            'status': 'success',
            'message': MessageLog.add(f'{char.name} equipped {item.name}.')
            })

    @app.route(
        '/item/unequip/<int:item_id>/char/<int:char_id>',
        methods=['POST'])
    def unequip_item(item_id, char_id):
        logger.debug(
            "%s\nequip_item(item_id=%d, char_id=%d)",
            "-" * 80, item_id, char_id)
        item = Item.load_complete_object(item_id)
        char = Character.load_complete_object(char_id)
        owned_item = char.owned_items.get(item_id)
        if not owned_item:
            return jsonify({
                'status': 'error',
                'message': "No item {item.name} in {char.name}\'s inventory."
                })
        owned_item.slot = ''
        char.to_db()
        return jsonify({
            'status': 'success',
            'message': MessageLog.add(f'{char.name} unequipped {item.name}.')
            })

    @app.route('/char/move/<int:char_id>'
                '/x_change/<int(signed=True):x_change>'
                '/y_change/<int(signed=True):y_change>',
                methods=['POST'])
    def move_char(char_id, x_change, y_change):
        logger.debug(
            "%s\nmove_char(%d,%d,%d)",
            "-" * 80, char_id, x_change, y_change)
        main_char = None
        req = RequestHelper('form')
        loc = None
        session['default_travel_with'] = req.get_str('travel_with')
        positions = {}
        for char in get_travel_chars(req, char_id):
            if not char:
                return "Character not found"
            if char.id == char_id:
                main_char = char
            session['default_move_char'] = char_id
            if not loc:
                loc = Location.load_complete_object(char.location.id)
            if not loc.grid.in_grid(char.position):
                char.position = loc.grid.default_pos
            cur_x, cur_y = char.position.as_tuple()
            newpos = NumTup((
                cur_x + x_change,
                cur_y + y_change))
            logger.debug("from (%s) to (%s)", char.position, newpos)
            if loc.grid.in_grid(newpos):
                char.position = newpos
                char.to_db()
            elif loc.grid.in_grid(char.position):
                pass  # don't move
            else:
                char.position = loc.grid.default_pos
                char.to_db()
            positions[char.id] = char.position.as_tuple()
        return jsonify({'positions': positions})

def back_to_referrer():
    referrer = session.pop('referrer', None)
    logger.debug("Referrer: %s", referrer)
    if referrer:
        return redirect(referrer)
    return redirect(url_for('configure_index'))

def error_page(exc):
    return render_template('error.html',
        message=str(exc),
        details=str(exc.original_exception))

def increment_name(name):
    """Add or increment a number at the end, for duplicating."""
    match = re.search(r'(.*?)(\d*)$', name)
    base_name = match.group(1)
    number = match.group(2)
    if number:
        new_number = str(int(number) + 1)
    else:
        new_number = "2"
    return f"{base_name}{new_number}"

def get_travel_chars(req, char_id):
    travel_with = req.get_str('travel_with')
    char_ids = [int(_id) for _id in travel_with.split(",") if _id] + [char_id]
    logger.debug("travel char ids: %s", char_ids)
    return Character.load_complete_objects(char_ids)
