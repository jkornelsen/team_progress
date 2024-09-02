from flask import (
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)
import logging

from .attrib import Attrib
from .character import Character, OwnedItem
from .item import Item
from .event import Event
from .location import Location, ItemAt
from .overall import Overall
from .utils import request_bool, request_int

logger = logging.getLogger(__name__)

class LinkLetters:
    """Letters to add before a link for hotkeys."""
    def __init__(self, excluded={'o'}):
        self.letter_index = 0
        self.letters = [
            chr(c) for c in range(ord('a'), ord('z') + 1)
            if chr(c) not in excluded]
        self.links = {}

    def next(self, link):
        if link in self.links:
            return self.links[link]
        if self.letter_index < len(self.letters):
            letter = self.letters[self.letter_index]
            self.letter_index += 1
            self.links[link] = letter
            return letter
        else:
            return ''

def back_to_referrer():
    referrer = session.pop('referrer', None)
    logger.debug(f"Referrer: %s", referrer)
    if referrer:
        return redirect(referrer)
    else:
        return redirect(url_for('configure'))

def error_page(exc):
    return render_template('error.html',
        message=str(exc),
        details=str(exc.original_exception))

def set_routes(app):
    @app.route('/configure/attrib/<attrib_id>', methods=['GET', 'POST'])
    def configure_attrib(attrib_id):
        attrib = Attrib.data_for_configure(attrib_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/attrib.html',
                current=attrib,
                game_data=g.game_data)
        try:
            attrib.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        return back_to_referrer()

    @app.route('/configure/character/<char_id>', methods=['GET', 'POST'])
    def configure_char(char_id):
        logger.debug("-" * 80)
        logger.debug("configure_char(%s)", char_id)
        char = Character.data_for_configure(char_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/character.html',
                current=char,
                game_data=g.game_data)
        try:
            char.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        return back_to_referrer()

    @app.route('/configure/event/<event_id>', methods=['GET', 'POST'])
    def configure_event(event_id):
        event = Event.data_for_configure(event_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/event.html',
                current=event,
                game_data=g.game_data)
        try:
            event.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        return back_to_referrer()

    @app.route('/configure/item/<item_id>', methods=['GET', 'POST'])
    def configure_item(item_id):
        logger.debug("-" * 80)
        logger.debug("configure_item(%s)", item_id)
        item = Item.data_for_configure(item_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/item.html',
                current=item,
                game_data=g.game_data)
        try:
            item.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        return back_to_referrer()

    @app.route('/configure/location/<loc_id>',methods=['GET', 'POST'])
    def configure_location(loc_id):
        loc = Location.data_for_configure(loc_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/location.html',
                current=loc,
                game_data=g.game_data)
        try:
            loc.configure_by_form()
        except DeletionError as e:
            return error_page(e)
        return back_to_referrer()

    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        game_data = Overall.data_for_configure()
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/overall.html',
                current=game_data.overall,
                game_data=game_data)
        game_data.overall.configure_by_form()
        return redirect(url_for('configure'))

    @app.route('/play/char/<int:char_id>')
    def play_char(char_id):
        logger.debug("-" * 80)
        logger.debug("play_char(%d)", char_id)
        instance = Character.data_for_play(char_id)
        if not instance:
            return 'Character not found'
        session['default_pickup_char'] = char_id
        return render_template(
            'play/character.html',
            current=instance,
            game_data=g.game_data,
            link_letters=LinkLetters(set('ost')))

    @app.route('/char/start/<int:char_id>', methods=['POST'])
    def start_char(char_id):
        logger.debug("-" * 80)
        logger.debug("start_char(%d)", char_id)
        dest_id = request_int(request, 'dest_id')
        char = Character.data_for_play(char_id)
        if not char.destination or char.destination.id != dest_id:
            char.destination = Location.get_by_id(dest_id)
            char.pile.quantity = 0
            char.to_db()
        if char.progress.start():
            char.to_db()
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/char/stop/<int:char_id>')
    def stop_char(char_id):
        logger.debug("-" * 80)
        logger.debug("stop_char(%d)", char_id)
        char = Character.data_for_play(char_id)
        if char.progress.stop():
            char.to_db()
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})

    @app.route('/char/progress_data/<int:char_id>')
    def char_progress_data(char_id):
        logger.debug("-" * 80)
        logger.debug("char_progress_data(%d)", char_id)
        char = Character.data_for_play(char_id)
        if char:
            current_loc_id = char.location.id if char.location else 0
            if not char.location or not char.destination:
                return jsonify({
                    'status': 'error',
                    'message': 'No travel destination.',
                    'current_loc_id': current_loc_id,
                    'quantity': 0})
            if char.progress.is_ongoing:
                time_spent = char.progress.determine_current_quantity()
                events = Event.load_triggers_for_loc(char.location.id)
                ignore_event_id = request_int(
                    request, 'ignore_event', '', 'args')
                for event in events:
                    if event.id != ignore_event_id:
                        if (event.trigger_by_duration
                                and event.check_trigger_for_duration(time_spent)):
                            return jsonify({
                                'status': 'interrupt',
                                'message': f"<h2>{char.name} triggered {event.name}!</h2>"
                                " Allow event?",
                                'event_id': event.id})
                char.to_db()
            if char.pile.quantity >= char.pile.item.q_limit:
                # arrived at the destination
                char.progress.stop()
                char.pile.quantity = 0
                char.location = char.destination
                char.destination = None
                char.to_db()
                return jsonify({
                    'status': 'arrived',
                    'current_loc_id': char.location.id})
            else:
                logger.debug("dest_id: %d", char.destination.id)
                return jsonify({
                    'status': 'ongoing',
                    'is_ongoing': char.progress.is_ongoing,
                    'current_loc_id': current_loc_id,
                    'dest_id': char.destination.id,
                    'quantity': int(char.pile.quantity),
                    'elapsed_time': char.progress.calculate_elapsed_time()})
        else:
            return jsonify({
                'status': 'error',
                'message': 'Character not found.'})

    @app.route('/play/event/<int:event_id>', methods=['GET', 'POST'])
    def play_event(event_id):
        logger.debug("-" * 80)
        logger.debug("play_event(%d)", event_id)
        instance = Event.data_for_configure(event_id)
        if not instance:
            return 'Event not found'
        if request.method == 'GET':
            return render_template(
                'play/event.html',
                current=instance,
                link_letters=LinkLetters(set('or')))
        instance.play_by_form()
        return render_template(
            'play/event.html',
            current=instance,
            outcome=instance.get_outcome(),
            link_letters=LinkLetters(set('or')))

    @app.route('/play/item/<int:item_id>/')
    def play_item(item_id):
        char_id = request_int(request, 'char_id', '', 'args')
        loc_id = request_int(request, 'loc_id', '', 'args')
        default_pile = request_bool(request, 'default_pile', False, 'args')
        if char_id:
            session['last_char_id'] = char_id
        if loc_id:
            session['last_loc_id'] = loc_id
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        logger.debug("-" * 80)
        logger.debug("play_item(item_id=%d, char_id=%s, loc_id=%s)",
            item_id, char_id, loc_id)
        item, container = Item.data_for_play(
            item_id, char_id, loc_id, default_pile=default_pile)
        if not item:
            return 'Item not found'
        defaults = {
            'pickup_char': session.get('default_pickup_char', ''),
            'movingto_char': session.get('default_movingto_char', ''),
            'slot': session.get('default_slot', '')
        }
        g.game_data.overall = Overall.from_db()
        return render_template(
            'play/item.html',
            current=item,
            container=container,
            char_id=char_id,
            loc_id=loc_id,
            defaults=defaults,
            game_data=g.game_data,
            link_letters=LinkLetters(set('cdelop')))

    @app.route('/item/progress_data/<int:item_id>/')
    def item_progress_data(item_id):
        char_id = request_int(request, 'char_id', '', 'args')
        loc_id = request_int(request, 'loc_id', '', 'args')
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        logger.debug("-" * 80)
        logger.debug("item_progress_data(item_id=%d, char_id=%s, loc_id=%s)",
            item_id, char_id, loc_id)
        item, container = Item.data_for_play(
            item_id, char_id, loc_id, default_pile=True)
        if not item:
            return jsonify({'error': 'Item not found'})
        logger.debug("Retrieved item %d from DB: %d recipes",
            item.id, len(item.recipes))
        if container.progress.is_ongoing:
            container.progress.determine_current_quantity()
        return jsonify({
            'is_ongoing': container.progress.is_ongoing,
            'recipe_id': container.progress.recipe.id,
            'quantity': container.pile.quantity,
            'elapsed_time': container.progress.calculate_elapsed_time()})

    @app.route('/item/start/<int:item_id>/<int:recipe_id>')
    def start_item(item_id, recipe_id):
        char_id = request_int(request, 'char_id', '', 'args')
        loc_id = request_int(request, 'loc_id', '', 'args')
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        logger.debug("-" * 80)
        logger.debug("start_item(item_id=%d, recipe_id=%d, char_id=%d, "
                     "loc_id=%d)", item_id, recipe_id, char_id, loc_id)
        _, container = Item.data_for_play(
            item_id, char_id, loc_id, default_pile=True)
        if container.progress.start(recipe_id):
            container.to_db()
            return jsonify({
                'status': 'success',
                'message': 'Progress started.',
                'is_ongoing': container.progress.is_ongoing})
        else:
            return jsonify({
                'status': 'error',
                'message': 'Could not start.',
                'is_ongoing': container.progress.is_ongoing})

    @app.route('/item/stop/<int:item_id>')
    def stop_item(item_id):
        char_id = request_int(request, 'char_id', '', 'args')
        loc_id = request_int(request, 'loc_id', '', 'args')
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        logger.debug("-" * 80)
        logger.debug("stop_item(%d)", item_id)
        item, container = Item.data_for_play(
            item_id, char_id, loc_id, default_pile=True)
        logger.debug("Retrieved item %d from DB: %d recipes",
            item.id, len(item.recipes))
        if container.progress.is_ongoing:
            container.progress.determine_current_quantity()
            container.progress.stop()
            container.to_db()
            return jsonify({
                'status': 'success',
                'message': 'Progress paused.',
                'is_ongoing': container.progress.is_ongoing})
        else:
            return jsonify({
                'status': 'success',
                'message': 'Progress is already paused.',
                'is_ongoing': container.progress.is_ongoing})

    @app.route('/item/gain/<int:item_id>/<int:recipe_id>', methods=['POST'])
    def gain_item(item_id, recipe_id):
        char_id = request_int(request, 'char_id', '', 'args')
        loc_id = request_int(request, 'loc_id', '', 'args')
        num_batches = request_int(request, 'quantity')
        if not char_id and not loc_id:
            char_id = session.get('last_char_id', '')
            loc_id = session.get('last_loc_id', '')
        logger.debug("-" * 80)
        logger.debug(
            "gain_item(item_id=%d, recipe_id=%d, num_batches=%d, char_id=%s, "
            "loc_id=%s)",
            item_id, recipe_id, num_batches, char_id, loc_id)
        item, container = Item.data_for_play(
            item_id, char_id, loc_id, default_pile=True)
        container.progress.set_recipe_by_id(recipe_id)
        changed = container.progress.change_quantity(num_batches)
        if changed:
            return jsonify({
                'status': 'success', 'message':
                f'Quantity of {item.name} changed.'})
        else:
            return jsonify({
                'status': 'error',
                'message': 'Could not change quantity.'})

    @app.route('/item/drop/<int:item_id>/char/<int:char_id>', methods=['POST'])
    def drop_item(item_id, char_id):
        logger.debug("-" * 80)
        logger.debug("drop_item(item_id=%d, char_id=%d)", item_id, char_id)
        item, container = Item.data_for_play(
            item_id, char_id, default_pile=False)
        char = container
        owned_item = next((oi for oi in char.items
            if oi.item.id == item_id), None)
        if not owned_item:
            return jsonify({
                'status': 'error',
                'message': f'No item {item.name} in {container.name}\'s inventory.'})
            return
        item_at = ItemAt(item=owned_item.item)
        item_at.quantity = owned_item.quantity
        loc = Location.data_for_configure(char.location.id)
        loc.items.append(item_at)  # TODO: check if it already exists
        char.items.remove(owned_item)
        loc.to_db()
        char.to_db()
        return jsonify({
            'status': 'success', 'message':
            f'Dropped {item.name}.'})

    @app.route('/item/pickup/<int:item_id>/loc/<int:loc_id>/char/<int:char_id>', methods=['POST'])
    def pickup_item(item_id, loc_id, char_id):
        logger.debug("-" * 80)
        logger.debug("pickup_item(item_id=%d, loc_id=%d, char_id=%d)", 
                     item_id, loc_id, char_id)
        session['default_pickup_char'] = char_id
        item, container = Item.data_for_play(
            item_id, at_loc_id=loc_id, default_pile=False)
        loc = container
        item_at = next((ia for ia in loc.items
            if ia.item.id == item_id), None)
        if not item_at:
            return jsonify({
                'status': 'error',
                'message': f'No item {item.name} at {container.name}.'})
            return
        owned_item = OwnedItem(item=item_at.item)
        owned_item.quantity = item_at.quantity
        char = Character.data_for_configure(char_id)
        char.items.append(owned_item)  # TODO: check if it already exists
        loc.items.remove(item_at)
        char.to_db()
        loc.to_db()
        return jsonify({
            'status': 'success', 'message':
            f'Picked up {item.name}.'})

    @app.route('/item/equip/<int:item_id>/char/<int:char_id>/slot/<string:slot>', methods=['POST'])
    def equip_item(item_id, char_id, slot):
        logger.debug("-" * 80)
        logger.debug("equip_item(item_id=%d, char_id=%d, slot=%d)", 
                     item_id, char_id, slot)
        session['default_slot'] = slot
        item, container = Item.data_for_play(
            item_id, char_id, default_pile=False)
        char = container
        owned_item = next((oi for oi in char.items
            if oi.item.id == item_id), None)
        if not owned_item:
            return jsonify({
                'status': 'error',
                'message': f'No item {item.name} in {container.name}\'s inventory.'})
            return
        owned_item.slot = slot
        char.to_db()
        return jsonify({
            'status': 'success', 'message':
            f'Equipped {item.name}.'})

    @app.route('/item/unequip/<int:item_id>/char/<int:char_id>', methods=['POST'])
    def unequip_item(item_id, char_id):
        logger.debug("-" * 80)
        logger.debug("equip_item(item_id=%d, char_id=%d)", item_id, char_id)
        item, container = Item.data_for_play(
            item_id, char_id, default_pile=False)
        char = container
        owned_item = next((oi for oi in char.items
            if oi.item.id == item_id), None)
        if not owned_item:
            return jsonify({
                'status': 'error',
                'message': f'No item {item.name} in {container.name}\'s inventory.'})
            return
        owned_item.slot = ''
        char.to_db()
        return jsonify({
            'status': 'success', 'message':
            f'Equipped {item.name}.'})

    @app.route('/play/location/<int:loc_id>')
    def play_location(loc_id):
        char_id = request_int(request, 'char_id', '', 'args')
        if char_id:
            session['last_char_id'] = char_id
        else:
            char_id = session.get('last_char_id', '')
        logger.debug("-" * 80)
        logger.debug("play_location(%d)", loc_id)
        instance = Location.data_for_play(loc_id)
        if not instance:
            return 'Location not found'
        return render_template(
            'play/location.html',
            current=instance,
            char_id=char_id,
            game_data=g.game_data,
            link_letters=LinkLetters())

    @app.route('/overview')
    def overview():
        overall, charlist, other_entities, interactions = (
            Overall.data_for_overview())
        return render_template(
            'play/overview.html',
            current=overall,
            charlist=charlist,
            other_entities=other_entities,
            interactions=interactions,
            link_letters=LinkLetters())

