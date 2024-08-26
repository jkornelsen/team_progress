from flask import g, request, session
import random
from types import SimpleNamespace

from .db_serializable import Identifiable, MutableNamespace, coldef
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
        self.difficulty = 10  # Moderate
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
        instance.determining_attrs = [
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
    def data_for_file(cls):
        print(f"{cls.__name__}.data_for_file()")
        # Get event data with attrib relation data
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.event_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """
        values = [g.game_token]
        tables_rows = cls.select_tables(
            query, values, ['events', 'event_attribs'])
        instances = {}  # keyed by ID
        for event_data, attrib_data in tables_rows:
            print(f"event_data {event_data}")
            print(f"attrib_data {attrib_data}")
            instance = instances.get(event_data.id)
            if not instance:
                instance = cls.from_json(vars(event_data))
                instances[event_data.id] = instance
            if attrib_data.attrib_id:
                if attrib_data.determining:
                    attriblist = instance.determining_attrs
                else:
                    attriblist = instance.changed_attrs
                attriblist.append(Attrib(attrib_data.attrib_id))
        # Get event data with trigger relation data
        triggers_data = cls.execute_select("""
            SELECT *
            FROM event_triggers
            WHERE game_token = %s
        """, values)
        for trigger_data in triggers_data:
            print(f"trigger_data {trigger_data}")
            instance = instances.get(trigger_data.event_id)
            if not instance:
                raise Exception(
                    f"Could not get instance for event id {event_data.id}")
            instance.triggers.append(
                Item(trigger_data.item_id) if trigger_data.item_id
                else Location(trigger_data.loc_id) if trigger_data.loc_id
                else None)
        # Print debugging info
        print(f"found {len(instances)} events")
        for instance in instances.values():
            print(f"event {instance.id} ({instance.name})"
                f" has {len(instance.triggers)} triggers"
                f" and {len(instance.determining_attrs)} det attrs")
        return list(instances.values())

    @classmethod
    def data_for_configure(cls, id_to_get):
        print(f"{cls.__name__}.data_for_configure()")
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        # Get all event data
        events_data = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
            ORDER BY {table}.name
        """, (g.game_token,))
        g.game_data.events = []
        current_data = MutableNamespace()
        for evt_data in events_data:
            if evt_data.id == id_to_get:
                current_data = evt_data
            g.game_data.events.append(Event.from_json(evt_data))
        # Get all attrib data and the current event's attrib relation data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.attrib_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.event_id = %s
            WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
        """, (id_to_get, g.game_token), ['attribs', 'event_attribs'])
        for attrib_data, evt_attrib_data in tables_rows:
            if evt_attrib_data.attrib_id:
                if evt_attrib_data.determining:
                    listname = 'determining_attrs'
                else:
                    listname = 'changed_attrs'
                current_data.setdefault(listname, []).append(attrib_data.id)
                print(f"current_data.{listname} = {current_data.get(listname)}")
            g.game_data.attribs.append(Attrib.from_json(attrib_data))
        # Get all item and location data and
        # the current events's trigger relation data
        for entity_cls in (Item, Location):
            join_key = "loc_id" if entity_cls == Location else "item_id"
            tables_rows = cls.select_tables("""
                SELECT *
                FROM {tables[0]}
                LEFT JOIN {tables[1]}
                    ON {tables[0]}.id = {tables[1]}.""" + join_key + """
                    AND {tables[0]}.game_token = {tables[1]}.game_token
                    AND {tables[1]}.event_id = %s
                WHERE {tables[0]}.game_token = %s
                ORDER BY {tables[0]}.name
            """, (id_to_get, g.game_token),
                [entity_cls.tablename(), 'event_triggers'])
            for entity_data, trigger_data in tables_rows:
                if trigger_data.event_id:
                    trigger_tup = (
                        entity_cls.basename(), trigger_data.get(join_key))
                    current_data.setdefault('triggers', []).append(trigger_tup)
                entity_cls.get_list().append(
                    entity_cls.from_json(entity_data))
        # Create event from data
        current_obj = Event.from_json(current_data)
        # Replace partial objects with fully populated objects
        for attrlist in (
                current_obj.determining_attrs,
                current_obj.changed_attrs):
            populated_objs = []
            for partial_attrib in attrlist:
                attrib = Attrib.get_by_id(partial_attrib.id)
                populated_objs.append(attrib)
            attrlist.clear()
            attrlist.extend(populated_objs)
        populated_objs = []
        for partial_obj in current_obj.triggers:
            cls = partial_obj.__class__
            lookup_obj = cls.get_by_id(partial_obj.id)
            populated_objs.append(lookup_obj)
        current_obj.triggers = populated_objs
        # Print debugging info
        print(f"found {len(current_obj.triggers)} triggers")
        print(f"found {len(current_obj.determining_attrs)} det att")
        if len(current_obj.triggers):
            trigger = current_obj.triggers[0]
            print(f"type={trigger.__class__.__name__}")
            print(f"id={trigger.id}")
            print(f"name={trigger.name}")
        return current_obj

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
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
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed event.'
            except DbError as e:
                raise DeletionError(e)
        elif 'cancel_changes' in request.form:
            print("Cancelling changes.")
        else:
            print("Neither button was clicked.")

    def play_by_form(self):
        print("Saving changes.")
        print(request.form)
        self.difficulty = self.form_int(request, 'difficulty')
        self.stat_adjustment = self.form_int(request, 'stat_adjustment')
        self.to_db()

    def get_outcome(self):
        if self.outcome_type == OUTCOME_FOURWAY:
            roll = roll_dice(20)
            total = roll + self.stat_adjustment - self.difficulty
            if total <= -OUTCOME_MARGIN:
                self.outcome = OUTCOME_CRITICAL_FAILURE
            elif total <= 0:
                self.outcome = OUTCOME_MINOR_FAILURE
            elif total < OUTCOME_MARGIN:
                self.outcome = OUTCOME_MINOR_SUCCESS
            else:
                self.outcome = OUTCOME_MAJOR_SUCCESS
            display = (
                "1d20 ({}) + Stat Adjustment {} - Difficulty {} = {}<br>"
                "Outcome is a {}."
            ).format(
                roll,
                self.stat_adjustment,
                self.difficulty,
                total,
                OUTCOMES[self.outcome],
            )
        elif self.outcome_type == OUTCOME_NUMERIC:
            range_min, range_max = self.numeric_range
            sides = range_max - range_min
            roll = roll_dice(sides)
            total = roll + range_min + self.stat_adjustment
            display = (
                "1d{} ({}) + Min {} + Stat Adjustment {}<br>"
                "Outcome = {}"
            ).format(
                sides,
                roll,
                range_min,
                self.stat_adjustment,
                total
            )
        elif self.outcome_type == OUTCOME_SELECTION:
            strings_list = self.selection_strings.split('\n')
            random_string = random.choice(strings_list)
            display = f"Outcome: {random_string}"
        else:
            raise(f"Unexpected outcome_type {self.outcome_type}")
        return display
