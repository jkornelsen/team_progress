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
import random
from types import SimpleNamespace

from .db_serializable import Identifiable, coldef, new_game_data
from .attrib import Attrib
from .item import Item
from .location import Location

OUTCOME_TYPES = [
    'fourway',  # critical/minor failure or success
    'numeric',  # such as a damage number
    'selection']  # random selection from a list
(OUTCOME_FOURWAY,
 OUTCOME_NUMERIC,
 OUTCOME_SELECTION) = OUTCOME_TYPES
OUTCOMES = [
    "Critical Failure",
    "Minor Failure",
    "Minor Success",
    "Major Success"]
(OUTCOME_CRITICAL_FAILURE,
 OUTCOME_MINOR_FAILURE,
 OUTCOME_MINOR_SUCCESS,
 OUTCOME_MAJOR_SUCCESS) = range(len(OUTCOMES))
BASE_DIFFICULTY = [
    SimpleNamespace(name='Easy', val=5),
    SimpleNamespace(name='Moderate', val=10),
    SimpleNamespace(name='Hard', val=15),
    SimpleNamespace(name='Very Hard', val=20)]
(DIFFICULTY_EASY,
 DIFFICULTY_MODERATE,
 DIFFICULTY_HARD,
 DIFFICULTY_VERY_HARD) = BASE_DIFFICULTY
OUTCOME_MARGIN = 9  # difference required to get major or critical

def roll_dice(sides):
    return random.randint(1, sides)

def create_trigger_entity(entity_name, entity_id):
    if entity_name == 'item':
        return Item(int(entity_id))
    elif entity_name == 'location':
        return Location(int(entity_id))
    else:
        raise ValueError(f"Unknown entity_name: {entity_name}")

tables_to_create = {
    'events': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        outcome_type varchar(20) not null,
        trigger_chance integer[],
        numeric_range integer[],
        selection_strings text
    """
}

class Event(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = True
        self.outcome_type = OUTCOME_FOURWAY
        self.numeric_range = (0, 10)  # (min, max)
        self.selection_strings = ""  # newline-separated possible outcomes
        self.determining_attrs = []  # Attrib objects determining the outcome
        self.changed_attrs = []  # Attrib objects changed by the outcome
        self.trigger_chance = (0, 1) # (numerator, denominator)
        self.triggers = []  # Item or Location objects that can trigger
        ## For a particular occurrence, not stored in Event table
        self.difficulty = 'Moderate'  # which one for a particular occurrence
        self.stat_adjustment = 0  # for example, 5 for perception
        self.advantage = 0  # for example +1 means best of two rolls
        self.outcome = 0

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'outcome_type': self.outcome_type,
            'numeric_range': self.numeric_range,
            'selection_strings': self.selection_strings,
            'determining_attrs': [
                attrib.id for attrib in self.determining_attrs],
            'changed_attrs': [
                attrib.id for attrib in self.changed_attrs],
            'trigger_chance': self.trigger_chance,
            'triggers': [
                (entity.basename(), entity.id)
                for entity in self.triggers]
        }

    @classmethod
    def from_json(cls, data, _=None):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.toplevel = data.get('toplevel', True)
        instance.outcome_type = data.get('outcome_type', OUTCOME_FOURWAY)
        instance.numeric_range = data.get('numeric_range', (0, 10))
        instance.selection_strings = data.get('selection_strings', "")
        instance.determing_attrs = [
            Attrib(int(attrib_id))
            for attrib_id in data.get('determining_attrs', [])]
        instance.changed_attrs = [
            Attrib(int(attrib_id))
            for attrib_id in data.get('changed_attrs', [])]
        instance.trigger_chance = data.get('trigger_chance', (0, 1))
        instance.triggers = [
            create_trigger_entity(entity_name, entity_id)
            for entity_name, entity_id in data.get('triggers', [])]
        return instance

    def json_to_db(self, doc):
        print(f"{self.__class__.__name__}.json_to_db()")
        print(f"doc={doc}")
        super().json_to_db(doc)
        for rel_table in ('event_attribs', 'event_triggers'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE event_id = %s AND game_token = %s
            """, (self.id, self.game_token))
        for determining in ('determining', 'changed'):
            attr_data = doc[f'{determining}_attrs']
            print(f"{determining}_attrs={attr_data}")
            if attr_data:
                values = [
                    (g.game_token, self.id, attrib_id,
                        determining == 'determining')
                    for attrib_id in attr_data]
                self.insert_multiple(
                    "event_attribs",
                    "game_token, event_id, attrib_id, determining",
                    values)
        if doc['triggers']:
            print(f"triggers: {doc['triggers']}")
            values = []
            for entity_name, entity_id in doc['triggers']:
                item_id = entity_id if entity_name == 'item' else None
                loc_id = entity_id if entity_name == 'location' else None
                values.append((g.game_token, self.id, item_id, loc_id))
            self.insert_multiple(
                "event_triggers",
                "game_token, event_id, item_id, loc_id",
                values)

    @classmethod
    def from_db(cls, id_to_get):
        return cls._from_db(id_to_get)

    @classmethod
    def list_from_db(cls):
        return cls._from_db()

    @classmethod
    def _from_db(cls, id_to_get=None):
        print(f"{cls.__name__}._from_db()")
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.event_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            LEFT JOIN {tables[2]}
                ON {tables[2]}.event_id = {tables[0]}.id
                AND {tables[2]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """
        values = [g.game_token]
        if id_to_get:
            query = f"{query}\nAND {{tables[0]}}.id = %s"
            values.append(id_to_get);
        tables_rows = cls.select_tables(
            query, values,
            ['events', 'event_attribs', 'event_triggers'])
        instances = {}  # keyed by ID
        for event_data, attrib_data, trigger_data in tables_rows:
            print(f"event_data {event_data}")
            print(f"attrib_data {attrib_data}")
            print(f"trigger_data {trigger_data}")
            instance = instances.get(event_data.id)
            if not instance:
                instance = cls.from_json(vars(event_data))
                instances[event_data.id] = instance
            if attrib_data.attrib_id:
                if attrib_data.determining:
                    attriblist = instance.determining_attribs
                else:
                    attriblist = instance.changed_attribs
                attriblist.append(Attrib(attrib_data.attrib_id))
            if trigger_data.event_id:
                instance.triggers.append(
                    Item(trigger_data.item_id) if trigger_data.item_id
                    else Location(trigger_data.loc_id) if trigger_data.loc_id
                    else None)
        # Print debugging info
        print(f"found {len(instances)} events")
        for instance in instances.values():
            print(f"event {instance.id} ({instance.name})"
                " has {len(instance.triggers)} triggers")
        # Convert and return
        instances = list(instances.values())
        if id_to_get is not None and len(instances) == 1:
            return instances[0]
        return instances

    @classmethod
    def data_for_configure(cls, config_id):
        print(f"{cls.__name__}.data_for_configure()")
        if config_id == 'new':
            config_id = 0
        else:
            config_id = int(config_id)
        # Get all names of needed entities
        for entity_cls in (Attrib, Item, Location):
            entities_data = cls.execute_select(f"""
                SELECT id, name
                FROM {entity_cls.tablename()}
                WHERE game_token = %s
                ORDER BY name
            """, (g.game_token,))
            setattr(
                g.game_data,
                entity_cls.listname(), [
                    entity_cls.from_json(entity_data)
                    for entity_data in entities_data])
        return cls.from_db(config_id)

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            entity_list = self.get_list()
            if self not in entity_list:
                entity_list.append(self)
            self.name = request.form.get('event_name')
            self.description = request.form.get('event_description')
            self.toplevel = bool(request.form.get('top_level'))
            self.outcome_type = request.form.get('outcome_type')
            self.numeric_range = (
                self.form_int(request, 'numeric_min', 0),
                self.form_int(request, 'numeric_max', 1))
            self.selection_strings = request.form.get('selection_strings', "")
            determining_attr_ids = request.form.getlist('determining_attr_id[]')
            changed_attr_ids = request.form.getlist('changed_attr_id[]')
            self.determining_attrs = [
                Attrib(int(attrib_id)) for attrib_id in determining_attr_ids]
            self.changed_attrs = [
                Attrib(int(attrib_id)) for attrib_id in changed_attr_ids]
            self.trigger_chance = (
                self.form_int(request, 'trigger_numerator', 1),
                self.form_int(request, 'trigger_denominator', 10))
            trigger_types = request.form.getlist('entity_type[]')
            trigger_ids = request.form.getlist('entity_id[]')
            self.triggers = [
                create_trigger_entity(entity_name, entity_id)
                for entity_name, entity_id in zip(trigger_types, trigger_ids)]
            self.to_db()
        elif 'delete_event' in request.form:
            self.remove_from_db()
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

    def play_by_form(self):
        if request.method == 'POST':
            print("Saving changes.")
            print(request.form)
            self.difficulty = request.form.get('event_difficulty')
            self.stat_adjustment = int(request.form.get('event_stat_adjustment'))
            self.to_db()
            return render_template(
                'play/event.html',
                current=self,
                outcome=self.get_outcome())
        else:
            return render_template(
                'play/event.html',
                current=self)

    def get_outcome(self):
        difficulty_value = self.difficulty_values[self.difficulty]
        roll = roll_dice(20)
        total = roll + self.stat_adjustment - difficulty_value
        if total <= -self.outcome_margin:
            self.outcome = OUTCOME_CRITICAL_FAILURE
        elif total <= 0:
            self.outcome = OUTCOME_MINOR_FAILURE
        elif total < self.outcome_margin:
            self.outcome = OUTCOME_MINOR_SUCCESS
        else:
            self.outcome = OUTCOME_MAJOR_SUCCESS
        display = (
            "1d20 ({}) + Stat Adjustment {} - Difficulty {} = {}<br>"
            "Outcome is a {}."
        ).format(
            roll,
            self.stat_adjustment,
            difficulty_value,
            total,
            OUTCOMES[self.outcome],
        )
        return display


def set_routes(app):
    @app.route('/configure/event/<event_id>', methods=['GET', 'POST'])
    def configure_event(event_id):
        new_game_data()
        instance = Event.data_for_configure(event_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/event.html',
                current=instance,
                game_data=g.game_data)
        else:
            return instance.configure_by_form()

    @app.route('/play/event/<int:event_id>', methods=['GET', 'POST'])
    def play_event(event_id):
        event = Event.get_by_id(event_id)
        if event:
            return event.play_by_form()
        else:
            return 'Event not found'

