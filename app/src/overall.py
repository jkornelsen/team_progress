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
    def __init__(self):
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
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls()
        instance.item = Item(int(data['item_id'])
            ) if data['item_id'] else None
        instance.quantity = data.get('quantity', 0)
        instance.character = Character(int(data['char_id'])
            ) if data['char_id'] else None
        instance.location = Location(int(data['loc_id'])
            ) if data['loc_id'] else None
        instance.attrib = Attrib(int(data['attrib_id'])
            ) if data['attrib_id'] else None
        instance.attrib_value = data.get('attrib_value', 0)
        return instance

    def id_to_refs_from_game_data(self):
        print(f"{self.__class__.__name__}.id_to_refs_from_game_data()")
        for entity_cls in (Item, Character, Location, Attrib):
            attr_name = entity_cls.basename()
            entity = getattr(self, attr_name)
            if entity is not None:
                entity_obj = entity_cls.get_by_id(entity.id)
                if entity_obj:
                    setattr(self, attr_name, entity_obj)
                    print(f"{attr_name} {entity.id} name {entity_obj.name}")
                else:
                    print(f"could not find {attr_name} {entity.id}")

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
        print(f"{cls.__name__}.from_db()")
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
        if not instance:
            instance = cls()
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
            winreq_ids = request.form.getlist('winreq_id')
            self.win_reqs = []
            for winreq_id in winreq_ids:
                prefix = f"winreq{winreq_id}_"
                req = WinRequirement()
                self.win_reqs.append(req)
                req.quantity = int(request.form.get(f"{prefix}quantity", 0))
                item_id = int(request.form.get(f"{prefix}item_id", 0))
                if item_id:
                    req.item = Item(item_id)
                char_id = int(request.form.get(f"{prefix}char_id", 0))
                if char_id:
                    req.character = Character(char_id)
                loc_id = int(request.form.get(f"{prefix}loc_id", 0))
                if loc_id:
                    req.location = Location(loc_id)
                attrib_id = int(request.form.get(f"{prefix}attrib_id", 0))
                if attrib_id:
                    req.attrib = Attrib(attrib_id)
                req.attrib_value = int(
                    request.form.get(f"{prefix}attribValue", 0))
            self.to_db()
        elif 'cancel_changes' in request.form:
            print("Cancelling changes.")
        else:
            print("Neither button was clicked.")
        return redirect(url_for('configure'))

    @classmethod
    def data_for_overview(cls):
        print("data_for_overview()")
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.location_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
                AND {tables[0]}.toplevel = TRUE
            ORDER BY {tables[0]}.name
        """, (g.game_token,), ['characters', 'locations'])
        CharacterRow = namedtuple('CharacterRow',
            ['char_id', 'char_name', 'loc_id', 'loc_name'])
        character_list = []
        for char_data, loc_data in tables_rows:
            row = CharacterRow(
                char_id=char_data.id,
                char_name=char_data.name,
                loc_id=loc_data.id,
                loc_name=loc_data.name)
            character_list.append(row)
        print(f"character_list={character_list}")
        item_data = cls.execute_select("""
            SELECT id, name
            FROM items
            WHERE toplevel = TRUE
                AND game_token = %s
        """, (g.game_token,))
        event_data = cls.execute_select("""
            SELECT id, name
            FROM events
            WHERE toplevel = TRUE
                AND game_token = %s
        """, (g.game_token,))
        other_toplevel_entities = SimpleNamespace(
            items=item_data, events=event_data)
        from .user_interaction import UserInteraction  # avoid circular import
        interactions_list = UserInteraction.recent_interactions()
        g.game_data.overall = cls.from_db()
        for win_req in g.game_data.overall.win_reqs:
            win_req.id_to_refs_from_game_data()
        return character_list, other_toplevel_entities, interactions_list

def set_routes(app):
    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        game_data = Overall.data_for_configure()
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/overall.html',
                current=game_data.overall,
                game_data=game_data)
        else:
            return game_data.overall.configure_by_form()

    @app.route('/overview')
    def overview():
        charlist, other_entities, interactions = Overall.data_for_overview()
        return render_template(
            'play/overview.html',
            current=g.game_data.overall,
            game_data=g.game_data,
            charlist=charlist,
            other_entities=other_entities,
            interactions=interactions)

