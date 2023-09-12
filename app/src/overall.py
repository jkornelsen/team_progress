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

    def fulfilled(self):
        """Is the conditions fulfilled?"""
        print(f"{self.__class__.__name__}.fulfilled()")
        if self.item and self.quantity > 0:
            print(f"require {self.quantity} of item {self.item.name}")
            have_qty = 0
            if self.location:
                for item_at in self.location.items:
                    if item_at.item.id == self.item.id:
                        have_qty += item_at.quantity
                for char in g.game_data.characters:
                    if char.location.id == self.location.id:
                        for owned_item in self.character.items:
                            if owned_item.item.id == self.item.id:
                                have_qty += owned_item.quantity
                print(f"have_qty={have_qty} at location")
            elif self.character:
                for owned_item in self.character.items:
                    if owned_item.item.id == self.item.id:
                        have_qty += owned_item.quantity
                print(f"have_qty={have_qty} owned by character")
            else:
                have_qty = self.item.progress.quantity
                print(f"have_qty={have_qty} in general storage")
            return have_qty >= self.quantity
        elif self.character:
            if self.location:
                print(f"require character {self.character.name}"
                    f" at location {self.location.name}")
                if (not self.character.location or
                        self.character.location.id != self.location.id):
                    return False
            if self.attrib:
                print(f"require character {self.character.name}"
                    f" attrib {self.attrib.name} value {self.attrib_value}")
                return self.character.attribs.get(
                    self.attrib, 0) > self.attrib_value
            return True

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
            " To start from scratch, change the title and this description"
            " in the Overall settings, and do initial"
            " setup such as adding a few items."
            "\r\n\r\n"
            "More setup can be done as the game goes along.")
        self.win_reqs = []

    @classmethod
    def tablename(cls):
        return 'overall'

    def have_won(self):
        if not self.win_reqs:
            return False
        for win_req in self.win_reqs:
            if not win_req.fulfilled():
                return False
        return True

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
            print("overall data not found -- returning generic object")
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
        instance = cls.from_db()
        g.game_data.overall = instance
        # Win requirement results
        for win_req in instance.win_reqs:
            win_req.id_to_refs_from_game_data()
        # TODO: split up into separate queries so rows don't multiply
        rows = cls.execute_select("""
            SELECT A.item_id, A.quantity,
                A.char_id, A.loc_id, A.attrib_id, A.attrib_value,
                B.quantity, C.quantity, D.quantity,
                E.char_id, F.attrib_value
            FROM win_requirements A
            LEFT JOIN items B  -- general storage
                ON B.id = A.item_id
                AND B.game_token = A.game_token
            LEFT JOIN loc_items C  -- at location
                ON C.loc_id = A.loc_id
                AND C.item_id = A.item_id
                AND C.game_token = A.game_token
            LEFT JOIN characters Dchar
                ON Dchar.location_id = A.loc_id
                AND Dchar.game_token = A.game_token
            LEFT JOIN char_items D  -- owned by character at location
                ON D.char_id = Dchar.id
                AND D.item_id = A.item_id
                AND D.game_token = A.game_token
            LEFT JOIN characters E  -- character at location
                ON E.char_id = A.char_id
                AND E.location_id = A.loc_id
                AND E.game_token = A.game_token
            LEFT JOIN char_attribs F  -- character at location
                ON F.char_id = A.char_id
                AND F.attrib_id = A.attrib_id
            WHERE A.game_token = %s
        """, (g.game_token,))
        for row in rows:
            pass
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

