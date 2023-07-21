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
import math
from sqlalchemy import (Column, String, Text, Boolean, Float, Integer,
    ForeignKeyConstraint,
    and_, text)
from sqlalchemy.orm import relationship

from db import db
from .attrib import Attrib, attrib_tbl
from .progress import Progress, progress_tbl
from .db_serializable import DbSerializable

item_tbl = DbSerializable.table_with_id(
    'item',
    Column('name', String(255), nullable=False),
    Column('description', Text, nullable=True),
    Column('toplevel', Boolean, nullable=False),
    Column('growable', Boolean, nullable=False),
    Column('result_qty', Float(precision=2), nullable=False),
    Column('progress_id', Integer, nullable=True))
item_tbl.append_constraint(
    ForeignKeyConstraint(
        [item_tbl.c.game_token, item_tbl.c.progress_id],
        [progress_tbl.c.game_token, progress_tbl.c.id]))

item_attribs = DbSerializable.table_with_token(
    'item_attribs',
    Column('item_id', Integer, primary_key=True),
    Column('attrib_id', Integer, primary_key=True))
item_attribs.append_constraint(
    ForeignKeyConstraint(
        [item_attribs.c.game_token, item_attribs.c.item_id],
        [item_tbl.c.game_token, item_tbl.c.id]))
item_attribs.append_constraint(
    ForeignKeyConstraint(
        [item_attribs.c.game_token, item_attribs.c.attrib_id],
        [attrib_tbl.c.game_token, attrib_tbl.c.id]))

item_sources = DbSerializable.table_with_token(
    'item_sources',
    Column('item_id', primary_key=True),
    Column('source_id', primary_key=True))
item_sources.append_constraint(
    ForeignKeyConstraint(
        [item_sources.c.game_token, item_sources.c.item_id],
        [item_tbl.c.game_token, item_tbl.c.id]))
item_sources.append_constraint(
    ForeignKeyConstraint(
        [item_sources.c.game_token, item_sources.c.source_id],
        [item_tbl.c.game_token, item_tbl.c.id]))

class Item(DbSerializable):
    __table__ = item_tbl

    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class

    attribs = relationship(
        Attrib, secondary=item_attribs,
        primaryjoin=and_(
            item_attribs.c.game_token == item_tbl.c.game_token,
            item_attribs.c.item_id == item_tbl.c.id),
        secondaryjoin=and_(
            item_attribs.c.game_token == attrib_tbl.c.game_token,
            item_attribs.c.attrib_id == attrib_tbl.c.id),
        backref='applies_to_items', lazy='dynamic')
    sources = relationship(
        'Item', secondary=item_sources,
        primaryjoin=and_(
            item_sources.c.game_token == item_tbl.c.game_token,
            item_sources.c.item_id == item_tbl.c.id),
        secondaryjoin=and_(
            item_sources.c.game_token == item_tbl.c.game_token,
            item_sources.c.source_id == item_tbl.c.id),
        backref='applies_to_items', lazy='dynamic')
    progress = relationship(
        Progress, backref='item_for_progress',
        foreign_keys=[item_tbl.c.game_token, item_tbl.c.progress_id],
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
        self.progress = Progress(self)
        self.growable = 'over_time'
        self.sources = {}  # keys Item object, values quantity required
        self.result_qty = 1  # how many one batch yields

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'growable': self.growable,
            'result_qty': self.result_qty,
            'sources': {
                str(item.id): quantity
                for item, quantity in self.sources.items()},
            'attribs': {
                str(attrib.id): val
                for attrib, val in self.attribs.items()},
            'progress': self.progress.to_json(),
        }

    @classmethod
    def from_json(cls, data, id_refs):
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        instance.toplevel = data['toplevel']
        instance.growable = data['growable']
        instance.result_qty = data.get('result_qty', 1)
        instance.progress = Progress.from_json(data['progress'], instance)
        instance.progress.step_size = instance.result_qty
        id_refs.setdefault('source', {})[instance.id] = {
            int(source_id): quantity
            for source_id, quantity in data['sources'].items()}
        instance.attribs = {
            Attrib.get_by_id(int(attrib_id)): val
            for attrib_id, val in data['attribs'].items()}
        cls.instances.append(instance)
        return instance

    @classmethod
    def list_with_references(cls, callback):
        id_refs = {}
        callback(id_refs)
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        for instance in cls.instances:
            instance.sources = {
                cls.get_by_id(source_id): quantity
                for source_id, quantity in
                id_refs.get('source', {}).get(instance.id, {}).items()}
            instance.progress.sources = instance.sources
        return cls.instances

    @classmethod
    def list_from_json(cls, json_data):
        def callback(id_refs):
            super(cls, cls).list_from_json(json_data, id_refs)
        return cls.list_with_references(callback)

    @classmethod
    def list_from_db(cls):
        def callback(id_refs):
            super(cls, cls).list_from_db(id_refs)
        return cls.list_with_references(callback)

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
                self.name = request.form.get('item_name')
                self.description = request.form.get('item_description')
                self.toplevel = bool(request.form.get('top_level'))
                self.growable = request.form.get('growable')
                self.result_qty = int(request.form.get('result_quantity'))
                source_ids = request.form.getlist('source_id')
                print(f"Source IDs: {source_ids}")
                self.sources = {}
                for source_id in source_ids:
                    source_quantity = int(
                        request.form.get(f'source_quantity_{source_id}', 0))
                    source_item = self.__class__.get_by_id(source_id)
                    self.sources[source_item] = source_quantity
                print("Sources: ", {source.name: quantity
                    for source, quantity in self.sources.items()})
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
                was_ongoing = self.progress.is_ongoing
                if was_ongoing:
                    self.progress.stop()
                else:
                    self.progress.quantity = int(request.form.get('item_quantity'))
                prev_quantity = self.progress.quantity
                self.progress = Progress(
                    self,
                    quantity=prev_quantity,
                    step_size=self.result_qty,
                    rate_amount=float(request.form.get('rate_amount')),
                    rate_duration=float(request.form.get('rate_duration')),
                    sources=self.sources)
                self.progress.limit = int(request.form.get('item_limit'))
                if was_ongoing:
                    self.progress.start()
                self.to_db()
            elif 'delete_item' in request.form:
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
                'configure/item.html',
                current=self,
                game_data=g.game_data)

def set_routes(app):
    @app.route('/configure/item/<item_id>', methods=['GET', 'POST'])
    def configure_item(item_id):
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_item(): {request.referrer}")
        if item_id == "new":
            print("Creating a new item.")
            item = Item()
        else:
            print(f"Retrieving item with ID: {item_id}")
            item = Item.get_by_id(int(item_id))
        return item.configure_by_form()

    @app.route('/play/item/<int:item_id>')
    def play_item(item_id):
        item = Item.get_by_id(item_id)
        if item:
            return render_template(
                'play/item.html',
                current=item,
                game_data=g.game_data)
        else:
            return 'Item not found'

    @app.route('/item/gain/<int:item_id>', methods=['POST'])
    def gain_item(item_id):
        quantity = int(request.form.get('quantity'))
        item = Item.get_by_id(item_id)
        num_batches = math.floor(quantity / item.progress.step_size)
        changed = item.progress.change_quantity(num_batches)
        if changed:
            return jsonify({
                'status': 'success', 'message':
                f'Quantity of {item.name} changed.'})
        else:
            return jsonify({
                'status': 'error',
                'message': 'Could not change quantity.'})

    @app.route('/item/progress_data/<int:item_id>')
    def item_progress_data(item_id):
        item = Item.get_by_id(item_id)
        if item:
            if item.progress.is_ongoing:
                item.progress.determine_current_quantity()
            return jsonify({
                'is_ongoing': item.progress.is_ongoing,
                'quantity': item.progress.quantity,
                'elapsed_time': item.progress.calculate_elapsed_time()})
        else:
            return jsonify({'error': 'Item not found'})

    @app.route('/item/start/<int:item_id>')
    def start_item(item_id):
        item = Item.get_by_id(item_id)
        if item.progress.start():
            item.to_db()
            return jsonify({'status': 'success', 'message': 'Progress started.'})
        else:
            return jsonify({'status': 'error', 'message': 'Could not start.'})

    @app.route('/item/stop/<int:item_id>')
    def stop_item(item_id):
        item = Item.get_by_id(item_id)
        if item.progress.stop():
            item.to_db()
            return jsonify({'message': 'Progress paused.'})
        else:
            return jsonify({'message': 'Progress is already paused.'})
