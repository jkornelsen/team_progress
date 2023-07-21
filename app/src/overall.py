from collections import namedtuple
from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from sqlalchemy import (
    ForeignKey, ForeignKeyConstraint,
    Integer, String, Text, Column, Integer, and_)
from sqlalchemy.orm import relationship, load_only

from db import db
from .item import Item, item_tbl
from .character import Character
from .db_serializable import DbSerializable

overall_tbl = DbSerializable.table_with_token(
    'overall',
    Column('title', String(255), nullable=False),
    Column('description', Text, nullable=True))

winning_items = DbSerializable.table_with_token(
    'winning_items',
    Column('item_id', ForeignKey(item_tbl.c.id), primary_key=True),
    Column('quantity', Integer, nullable=False))
winning_items.append_constraint(
    ForeignKeyConstraint(
        [winning_items.c.game_token, winning_items.c.item_id],
        [item_tbl.c.game_token, item_tbl.c.id]))

class Overall(DbSerializable):
    """Overall scenario settings such as scenario title and goal,
    and app settings."""
    __table__ = overall_tbl

    items = relationship(
        Item, secondary=winning_items,
        primaryjoin=(
            winning_items.c.game_token == overall_tbl.c.game_token),
        secondaryjoin=and_(
            winning_items.c.game_token == item_tbl.c.game_token,
            winning_items.c.item_id == item_tbl.c.id),
        backref='item_wins_for', lazy='dynamic')

    def __init__(self):
        self.title = "Generic Adventure"
        self.description = (
            "An empty scenario."
            " To start with, change the title and this description"
            " in the Overall settings, and do some basic"
            " setup such as adding some items.")
        self.winning_items = {}  # Item objects with quantity required.

    def to_json(self):
        return {
            'title': self.title,
            'description': self.description,
            'winning_item': self.winning_item.id if self.winning_item else None,
            'winning_quantity': self.winning_quantity
        }

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.title = data['title']
        instance.description = data['description']
        winning_item_id = data['winning_item']
        if winning_item_id is not None:
            instance.winning_item = Item.get_by_id(int(winning_item_id))
            instance.winning_quantity = data['winning_quantity']
        else:
            instance.winning_item = None
            instance.winning_quantity = 0
        return instance

    @classmethod
    def from_db(cls):
        print(f"{cls.__name__}.from_db()")
        collection = cls.get_collection()
        doc = collection.find_one({'game_token': g.game_token})
        if doc is None:
            print("doc not found -- returning generic object")
            return cls()
        return cls.from_json(doc)

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:
                print("Saving changes.")
                print(request.form)
                self.title = request.form.get('scenario_title')
                self.description = request.form.get('scenario_description')
                winning_item_ids = request.form.getlist('winning_item_id')
                print(f"Source IDs: {winning_item_ids}")
                self.winning_items = {}
                for winning_item_id in winning_item_ids:
                    winning_item_quantity = int(
                        request.form.get(f'winning_item_quantity_{winning_item_id}', 0))
                    winning_item = self.__class__.get_by_id(winning_item_id)
                    self.winning_items[winning_item] = winning_item_quantity
                print("Sources: ", {winning_item.name: quantity
                    for winning_item, quantity in self.winning_items.items()})

                winning_item_id = request.form.get('winning_item')
                if winning_item_id:
                    self.winning_item = Item.get_by_id(int(winning_item_id))
                    self.winning_quantity = int(request.form.get('winning_quantity'))
                else:
                    self.winning_item = None
                    self.winning_quantity = 1
                db.session.commit()
                #self.to_db()
            elif 'cancel_changes' in request.form:
                print("Cancelling changes.")
            else:
                print("Neither button was clicked.")
            return redirect(url_for('configure'))
        else:
            return render_template(
                'configure/overall.html',
                current=self,
                game_data=g.game_data)

CharacterRow = namedtuple('CharacterRow',
    ['char_id', 'char_name', 'loc_id', 'loc_name',
    'action_name', 'action_link', 'username'])

def get_charlist_display():
    docs = Character.query.options(
        load_only(Character.name, Character.id, Character.toplevel)).filter_by(
        game_token=g.game_token).all()
    char_data = [character.__dict__ for character in docs]
    # SELECT B.name, B.id
    # FROM Character A, Location B
    # WHERE B.id = A.location_id
    character_rows = [] # Create a list to hold the character rows
    for char in char_data:
        if char.toplevel:
            row = CharacterRow(
                char_id=char.id,
                char_name=char.name,
                loc_id=char.location.id if char.location else None,
                loc_name=char.location.name if char.location else None,
                action_name="TODO",
                action_link="TODO",
                username=None
            )
            character_rows.append(row)
    from .user_interaction import UserInteraction
    interactions = UserInteraction.recent_interactions()
    # Combine user records with rows containing the same character.
    for interaction in interactions:
        if interaction.char:
            modified_rows = []
            for row in character_rows:
                if row.char_id == interaction.char.id:
                    modified_row = row._replace(
                        username=interaction.username,
                        action_name=interaction.action_name(),
                        action_link=interaction.action_link())
                    modified_rows.append(modified_row)
                else:
                    modified_rows.append(row)
            character_rows = modified_rows
    # Add separate rows for each username of the same the game token
    # that is not in the character list
    for interaction in interactions:
        if interaction.username not in [row.username for row in character_rows]:
            row = CharacterRow(
                char_id=interaction.char.id if interaction.char else -1,
                char_name=interaction.char.name if interaction.char else "",
                loc_id=None,
                loc_name=None,
                action_name=interaction.action_name(),
                action_link=interaction.action_link(),
                username=interaction.username)
            character_rows.append(row)
    # not row.username ensures that rows without a username (empty or None) come
    # before rows with a username.
    # row.username or '' handles the case where row.username is None or an empty
    # string, ensuring consistent sorting within rows without a username.
    # row.char_name is used as the primary sorting criterion, ensuring
    # alphabetical sorting within rows.
    character_rows.sort(key=lambda row:
        (not row.username, row.username or '', row.char_name))
    return character_rows

def set_routes(app):
    @app.route('/overview')
    def overview():
        overall = Overall()
        return render_template(
            'play/overview.html',
            current=overall,
            charlist=get_charlist_display())

    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        return g.game_data.overall.configure_by_form()

