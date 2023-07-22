from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from sqlalchemy import (
    Column, String, Text, Boolean, Integer,
    ForeignKeyConstraint, and_)
from sqlalchemy.orm import relationship

from database import db
from .db_serializable import DbSerializable, table_with_id, table_with_token
from .attrib import Attrib, attrib_tbl
from .item import Item, item_tbl
from .location import Location, loc_tbl
from .progress import Progress, progress_tbl

char_tbl = table_with_id(
    'character',
    Column('name', String(255), nullable=False),
    Column('description', Text, nullable=True),
    Column('toplevel', Boolean, nullable=False),
    Column('location_id', Integer),
    Column('progress_id', Integer, nullable=True),
    Column('destination_id', Integer))
char_tbl.append_constraint(
    ForeignKeyConstraint(
        [char_tbl.c.game_token, char_tbl.c.progress_id],
        [progress_tbl.c.game_token, progress_tbl.c.id]))

char_attribs = table_with_token(
    'char_attribs',
    Column('char_id', Integer, primary_key=True),
    Column('attrib_id', Integer, primary_key=True))
char_attribs.append_constraint(
    ForeignKeyConstraint(
        [char_attribs.c.game_token, char_attribs.c.char_id],
        [char_tbl.c.game_token, char_tbl.c.id]))
char_attribs.append_constraint(
    ForeignKeyConstraint(
        [char_attribs.c.game_token, char_attribs.c.attrib_id],
        [attrib_tbl.c.game_token, attrib_tbl.c.id]))

char_items = table_with_token(
    'char_items',
    Column('char_id', primary_key=True),
    Column('item_id', primary_key=True))
char_items.append_constraint(
    ForeignKeyConstraint(
        [char_items.c.game_token, char_items.c.char_id],
        [char_tbl.c.game_token, char_tbl.c.id]))
char_items.append_constraint(
    ForeignKeyConstraint(
        [char_items.c.game_token, char_items.c.item_id],
        [item_tbl.c.game_token, item_tbl.c.id]))

class Character(DbSerializable):
    __table__ = char_tbl

    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class

    attribs = relationship(
        Attrib, secondary=char_attribs,
        primaryjoin=and_(
            char_attribs.c.game_token == char_tbl.c.game_token,
            char_attribs.c.char_id == char_tbl.c.id),
        secondaryjoin=and_(
            char_attribs.c.game_token == attrib_tbl.c.game_token,
            char_attribs.c.attrib_id == attrib_tbl.c.id),
        backref='affects_char', lazy='dynamic')
    items = relationship(
        Item, secondary=char_items,
        primaryjoin=and_(
            char_items.c.game_token == char_tbl.c.game_token,
            char_items.c.char_id == char_tbl.c.id),
        secondaryjoin=and_(
            char_items.c.game_token == item_tbl.c.game_token,
            char_items.c.item_id == item_tbl.c.id),
        backref='owned_by', lazy='dynamic')
    location = relationship(
        Location, backref='char_at',
        primaryjoin=and_(
            char_tbl.c.game_token == loc_tbl.c.game_token,
            char_tbl.c.location_id == loc_tbl.c.id),
        foreign_keys={
            char_tbl.c.game_token: loc_tbl.c.game_token,
            char_tbl.c.location_id: loc_tbl.c.id},
        lazy=True, uselist=False)
    destination = relationship(
        Location, backref='char_dest',
        primaryjoin=and_(
            char_tbl.c.game_token == loc_tbl.c.game_token,
            char_tbl.c.location_id == loc_tbl.c.id),
        foreign_keys={
            char_tbl.c.game_token: loc_tbl.c.game_token,
            char_tbl.c.destination_id: loc_tbl.c.id},
        lazy=True, uselist=False)
    progress = relationship(
        Progress, backref='char_for_progress',
        lazy=True, uselist=False)

    def __init__(self, new_id='auto'):
        if new_id == 'auto':
            self.__class__.last_id += 1
            self.id = self.__class__.last_id
        else:
            self.id = new_id
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.__class__.instances) > 1 else True
        self.attribs = {}  # keys are Attrib object, values are stat val
        self.items = {}  # keys are Item object, values are slot name
        self.location = None  # Location object
        self.progress = Progress(self)  # for travel or perhaps other actions
        self.destination = None  # Location object to travel to

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'attribs': {
                str(attrib.id): val
                for attrib, val in self.attribs.items()},
            'items': {
                str(item.id): slot
                for item, slot in self.items.items()},
            'location_id': self.location.id if self.location else None,
            'progress': self.progress.to_json(),
            'dest_id': self.destination.id if self.destination else None
        }

    @classmethod
    def from_json(cls, data, _):
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        instance.toplevel = data['toplevel']
        instance.items = {
            Item.get_by_id(int(item_id)): slot
            for item_id, slot in data['items'].items()}
        instance.attribs = {
            Attrib.get_by_id(int(attrib_id)): val
            for attrib_id, val in data['attribs'].items()}
        instance.location = Location.get_by_id(
            int(data['location_id'])) if data['location_id'] else None
        instance.progress = Progress.from_json(data['progress'], instance)
        instance.destination = Location.get_by_id(
            int(data['dest_id'])) if data['dest_id'] else None
        cls.instances.append(instance)
        return instance

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
                self.name = request.form.get('char_name')
                self.description = request.form.get('char_description')
                self.toplevel = bool(request.form.get('top_level'))
                item_ids = request.form.getlist('item_id[]')
                item_slots = request.form.getlist('item_slot[]')
                self.items = {}
                for item_id, item_slot in zip(item_ids, item_slots):
                    item = Item.get_by_id(int(item_id))
                    if item:
                        self.items[item] = item_slot
                location_id = request.form.get('char_location')
                self.location = Location.get_by_id(
                    int(location_id)) if location_id else None
                attrib_ids = request.form.getlist('attrib_id')
                print(f"Attrib IDs: {attrib_ids}")
                self.attribs = {}
                for attrib_id in attrib_ids:
                    attrib_val = int(
                        request.form.get(f'attrib_val_{attrib_id}', 0))
                    attrib_item = Attrib.get_by_id(attrib_id)
                    self.attribs[attrib_item] = attrib_val
                print("attribs: ", {attrib.name: val
                    for attrib, val in self.attribs.items()})
                self.to_db()
            elif 'delete_character' in request.form:
                self.__class__.instances.remove(self)
                self.__class__.remove_from_db(self.id)
            elif 'cancel_changes' in request.form:
                print("Cancelling changes.")
            else:
                print("Neither button was clicked.")
            referrer = session.pop('referrer', None)
            print(f"Referrer in configure_by_form(): {referrer}")
            if referrer:
                return redirect(referrer)
            else:
                return redirect(url_for('configure'))
        else:
            return render_template(
                'configure/character.html',
                current=self,
                game_data=g.game_data)

def set_routes(app):
    @app.route('/configure/char/<char_id>', methods=['GET', 'POST'])
    def configure_char(char_id):
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_char(): {request.referrer}")
        if char_id == "new":
            print("Creating a new character.")
            char = Character()
        else:
            print(f"Retrieving character with ID: {char_id}")
            char = Character.get_by_id(int(char_id))
        return char.configure_by_form()

    @app.route('/play/char/<int:char_id>')
    def play_char(char_id):
        char = Character.get_by_id(char_id)
        if char:
            return render_template(
                'play/character.html',
                current=char)
        else:
            return 'Character not found'

    @app.route('/char/start/<int:char_id>', methods=['POST'])
    def start_char(char_id):
        dest_id = int(request.form.get('dest_id'))
        char = Character.get_by_id(char_id)
        if not char.destination or char.destination.id != dest_id:
            char.destination = Location.get_by_id(dest_id)
            char.progress.quantity = 0
            char.to_db()
        if char.progress.start():
            char.to_db()
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/char/stop/<int:char_id>')
    def stop_char(char_id):
        char = Character.get_by_id(char_id)
        if char.progress.stop():
            char.to_db()
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})

    @app.route('/char/progress_data/<int:char_id>')
    def char_progress_data(char_id):
        char = Character.get_by_id(char_id)
        if char:
            if not char.location or not char.destination:
                return jsonify({
                    'quantity': 0,
                    'is_ongoing': False,
                    'message': 'No travel destination.'})
            if char.progress.is_ongoing:
                char.progress.determine_current_quantity()
                char.to_db()
            distance = char.location.destinations[char.destination]
            if char.progress.quantity >= distance:
                # arrived at the destination
                char.progress.stop()
                char.progress.quantity = 0
                char.location = char.destination
                char.destination = None
                char.to_db()
                return jsonify({'status': 'arrived'})
            else:
                return jsonify({
                    'is_ongoing': char.progress.is_ongoing,
                    'quantity': int(char.progress.quantity),
                    'elapsed_time': char.progress.calculate_elapsed_time()})
        else:
            return jsonify({'error': 'Character not found.'})

