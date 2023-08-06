from collections import namedtuple
from types import SimpleNamespace
from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from .db_serializable import DbSerializable, coldef

from .attrib import Attrib
from .character import Character
from .event import Event
from .item import Item
from .location import Location

tables_to_create = {
    'overall': f"""
        {coldef('game_token')},
        title varchar(255) NOT NULL,
        {coldef('description')},
        PRIMARY KEY (game_token)
    """
}

class WinRequirement:
    """One of:
        * Items with qty and Attrib, at Location or Character
        * Characters with Attrib at Location
    """
    def __init__(self, new_id=0):
        self.item = None
        self.quantity = 0
        self.character = None
        self.location = None
        self.attrib = None
        self.attrib_value = 0

    def to_json(self):
        return {
            'item_id': self.item.id if self.item else None,
            'quantity': self.quantity,
            'char_id': self.character.id if self.character else None,
            'loc_id': self.location.id if self.location else None,
            'attrib_id': self.attrib.id if self.attrib else None,
            'attrib_value': self.attrib_value}

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.item = Item(int(data['item_id'])
            ) if data['item_id'] else None,
        instance.quantity = data.get('quantity', 0),
        instance.character = Character(int(data['char_id'])
            ) if data['char_id'] else None,
        instance.location = Location(int(data['loc_id'])
            ) if data['loc_id'] else None,
        instance.attrib = Attrib(int(data['attrib_id'])
            ) if data['attrib_id'] else None,
        instance.attrib_value = data.get('attrib_value', 0),
        return instance

    def id_to_refs_from_game_data(self):
        for attr_name in ['item', 'character', 'location', 'attrib']:
            entity_list = getattr(g.game_data, attr_name + "s")
            entity = getattr(self, attr_name)
            if entity is not None:
                entity_id = entity.id
                if entity_id in entity_list:
                    setattr(self, attr_name, entity_list[entity_id])

class Overall(DbSerializable):
    """Overall scenario settings such as scenario title and goal,
    and app settings."""

    def __init__(self):
        self.title = "Generic Adventure"
        self.description = (
            "An empty scenario."
            " To start with, change the title and this description"
            " in the Overall settings, and do some basic"
            " setup such as adding some items.")
        self.win_reqs = []

    @classmethod
    def tablename(cls):
        return 'overall'

    def to_json(self):
        return {
            'title': self.title,
            'description': self.description,
            'win_reqs': [
                win_req.to_json()
                for win_req in self.win_reqs],
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls()
        instance.title = data['title']
        instance.description = data['description']
        instance.win_reqs = [
            WinRequirement.from_json(winreq_data)
            for winreq_data in data.get('win_reqs', [])]
        return instance

    @classmethod
    def from_db(cls):
        data = DbSerializable.execute_select("""
            SELECT *
            FROM overall
            WHERE game_token = %s
        """, (g.game_token,))
        if not data:
            print("overall data not found -- returning generic object")
            return cls()
        return cls.from_json(data)

    @classmethod
    def from_db(cls):
        print(f"{cls.__name__}._from_db()")
        #if 'overall' in g:
        #    return g.overall
        values = [g.game_token]
        tables_rows = DbSerializable.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """, (g.game_token,), ('overall', 'win_requirements'))
        instance = None
        for overall_data, winreq_data in tables_rows:
            if not instance:
                instance = cls.from_json(vars(overall_data))
            if winreq_data.item_id or winreq_data.char_id:
                instance.win_reqs.append(
                    WinRequirement.from_json(winreq_data))
        return instance

    def json_to_db(self, doc):
        super().json_to_db(doc)
        self.execute_change(f"""
            DELETE FROM win_requirements
            WHERE game_token = %s
        """, (g.game_token,))
        self.insert_multiple_from_dict(
            "win_requirements", doc['win_reqs'])

    @classmethod
    def data_for_configure(cls):
        print(f"{cls.__name__}.data_for_configure()")
        from .game_data import GameData
        game_data = GameData.entity_names_from_db()
        game_data.overall = cls.from_db()
        for win_req in game_data.overall.win_reqs:
            win_req.id_to_refs_from_game_data()
        return game_data

    def configure_by_form(self):
        if 'save_changes' in request.form:
            print("Saving changes.")
            print(request.form)
            self.title = request.form.get('scenario_title')
            self.description = request.form.get('scenario_description')
            winning_item_ids = request.form.getlist('winning_item_id')
            print(f"Source IDs: {winning_item_ids}")
            self.win_reqs = {}
            for winning_item_id in winning_item_ids:
                winning_item_quantity = int(
                    request.form.get(f'winning_item_quantity_{winning_item_id}', 0))
                winning_item = self.get_by_id(winning_item_id)
                self.win_reqs[winning_item] = winning_item_quantity
            print("Sources: ", {winning_item.name: quantity
                for winning_item, quantity in self.win_reqs.items()})
            winning_item_id = request.form.get('winning_item')
            if winning_item_id:
                self.winning_item = Item.get_by_id(int(winning_item_id))
                self.winning_quantity = int(request.form.get('winning_quantity'))
            else:
                self.winning_item = None
                self.winning_quantity = 1
            self.to_db()
        elif 'cancel_changes' in request.form:
            print("Cancelling changes.")
        else:
            print("Neither button was clicked.")
        return redirect(url_for('configure'))

CharacterRow = namedtuple('CharacterRow',
    ['char_id', 'char_name', 'loc_id', 'loc_name',
    'action_name', 'action_link', 'username'])

def get_charlist_display():
    char_data = DbSerializable.execute_select("""
        SELECT c.name, c.id, c.toplevel, c.location_id,
            l.name as location_name
        FROM characters c
        LEFT OUTER JOIN locations l
            ON c.location_id = l.id AND c.game_token = l.game_token
        WHERE c.game_token = %s
    """, (g.game_token,))
    # SELECT B.name, B.id
    # FROM Character A, Location B
    # WHERE B.id = A.location_id
    character_rows = [] # Create a list to hold the character rows
    for char in char_data:
        if char.toplevel:
            row = CharacterRow(
                char_id=char.id,
                char_name=char.name,
                loc_id=char.location_id,
                loc_name=char.location_name,
                action_name="TODO",
                action_link="TODO",
                username=None
            )
            character_rows.append(row)
    from .user_interaction import UserInteraction  # avoid circular import
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
    print(f"character_rows={character_rows}")
    return character_rows

def get_items_and_events():
    item_data = DbSerializable.execute_select("""
        SELECT id, name
        FROM items
        WHERE toplevel = TRUE
            AND game_token = %s
    """, (g.game_token,))
    event_data = DbSerializable.execute_select("""
        SELECT id, name
        FROM events
        WHERE toplevel = TRUE
            AND game_token = %s
    """, (g.game_token,))
    return SimpleNamespace(items=item_data, events=event_data)

def set_routes(app):
    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        if request.method == 'GET':
            game_data = Overall.data_for_configure()
            return render_template(
                'configure/overall.html',
                current=game_data.overall,
                game_data=game_data)
        else:
            return Overall().configure_by_form()

    @app.route('/overview')
    def overview():
        other_entities = get_items_and_events()
        overall = Overall()
        return render_template(
            'play/overview.html',
            current=overall,
            charlist=get_charlist_display(),
            other_entities=get_items_and_events())

