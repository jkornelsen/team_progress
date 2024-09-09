from flask import g, request, session
import logging
import random
from types import SimpleNamespace

from .attrib import Attrib
from .db_serializable import Identifiable, MutableNamespace, coldef
from .item import Item
from .location import Location
from .utils import request_bool, request_int

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

logger = logging.getLogger(__name__)

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
        toplevel boolean NOT NULL,
        outcome_type varchar(20) not null,
        trigger_chance integer[],
        trigger_by_duration boolean,
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
        self.trigger_by_duration = True  # during progress or when finished
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
            'trigger_by_duration': self.trigger_by_duration,
            'triggers': [
                (entity.basename(), entity.id)
                for entity in self.triggers]
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.toplevel = data.get('toplevel', True)
        instance.outcome_type = data.get('outcome_type', OUTCOME_FOURWAY)
        instance.numeric_range = tuple(data.get('numeric_range', (0, 10)))
        instance.selection_strings = data.get('selection_strings', "")
        instance.determining_attrs = [
            Attrib(int(attrib_id))
            for attrib_id in data.get('determining_attrs', [])]
        instance.changed_attrs = [
            Attrib(int(attrib_id))
            for attrib_id in data.get('changed_attrs', [])]
        instance.trigger_chance = tuple(data.get('trigger_chance', (0, 1)))
        instance.trigger_by_duration = data.get('trigger_by_duration', True)
        instance.triggers = [
            create_trigger_entity(entity_name, entity_id)
            for entity_name, entity_id in data.get('triggers', [])]
        return instance

    def json_to_db(self, doc):
        logger.debug("json_to_db()")
        logger.debug("doc=%s", doc)
        super().json_to_db(doc)
        for rel_table in ('event_attribs', 'event_triggers'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE event_id = %s AND game_token = %s
            """, (self.id, self.game_token))
        for determining in ('determining', 'changed'):
            attr_data = doc[f'{determining}_attrs']
            logger.debug("%s_attrs=%s", determining, attr_data)
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
            logger.debug("triggers: %s", doc['triggers'])
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
    def load_complete_object(cls, id_to_get):
        """Load an object with everything needed for storing to db.
        Like data_for_file() but only one object.
        """
        logger.debug("load_complete_object(%s)", id_to_get)
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        # Get this event's base data
        events_row = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
                AND id = %s
        """, (g.game_token, id_to_get), fetch_all=False)
        current_data = MutableNamespace()
        if events_row:
            current_data = event_row
        # Get this event's attrib relation data
        event_attribs_rows = cls.execute_select("""
            SELECT *
            FROM event_attribs
            WHERE game_token = %s
                AND event_id = %s
        """, (g.game_token, id_to_get))
        for attrib_data in event_attribs_rows:
            if attrib_data.determining:
                listname = 'determining_attrs'
            else:
                listname = 'changed_attrs'
            current_data.setdefault(listname, []).append(
                attrib_data.attrib_id)
        # Get this events's trigger relation data
        triggers_rows = cls.execute_select("""
            SELECT *
            FROM event_triggers
            WHERE game_token = %s
                AND event_id = %s
        """, (g.game_token, id_to_get))
        for trigger_data in triggers_rows:
            if trigger_data.item_id:
                trigger_tup = (Item, trigger_data.item_id)
            else:
                trigger_tup = (Location, trigger_data.loc_id)
            current_data.setdefault('triggers', []).append(trigger_tup)
        # Create event from data
        return Event.from_json(current_data)

    @classmethod
    def data_for_file(cls):
        logger.debug("data_for_file()")
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
            logger.debug("event_data %s", event_data)
            logger.debug("attrib_data %s", attrib_data)
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
        triggers_rows = cls.execute_select("""
            SELECT *
            FROM event_triggers
            WHERE game_token = %s
        """, values)
        for trigger_row in triggers_rows:
            logger.debug("trigger_row %s", trigger_row)
            instance = instances.get(trigger_row.event_id)
            if not instance:
                raise Exception(
                    f"Could not get instance for event id {event_data.id}")
            instance.triggers.append(
                Item(trigger_row.item_id) if trigger_row.item_id
                else Location(trigger_row.loc_id) if trigger_row.loc_id
                else None)
        # Print debugging info
        logger.debug(f"found %d events", len(instances))
        for instance in instances.values():
            logger.debug("event %d (%s) has %d triggers and %d det attrs",
            instance.id, instance.name, len(instance.triggers),
            len(instance.determining_attrs))
        return list(instances.values())

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_object(id_to_get)
        # Get all basic data
        g.game_data.from_db_flat([Attrib, Event, Item, Location])
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
        logger.debug("found %d triggers", len(current_obj.triggers))
        logger.debug("found %d det att", len(current_obj.determining_attrs))
        if len(current_obj.triggers):
            trigger = current_obj.triggers[0]
            logger.debug("type=%s", trigger.__class__.__name__)
            logger.debug("id=%d", trigger.id)
            logger.debug("name=%s", trigger.name)
        return current_obj

    @classmethod
    def load_triggers_for_loc(cls, loc_id):
        logger.debug("load_triggers_for_loc()")
        events_rows = cls.execute_select("""
            SELECT {table}.*
            FROM event_triggers
            INNER JOIN {table}
                ON {table}.id = event_triggers.event_id
                AND {table}.game_token = event_triggers.game_token
            WHERE event_triggers.game_token = %s
                AND event_triggers.loc_id = %s
        """, (g.game_token, loc_id))
        g.game_data.events = []
        for event_row in events_rows:
            g.game_data.events.append(Event.from_json(event_row))
        return g.game_data.events

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            logger.debug("Saving changes.")
            logger.debug(request.form)
            self.name = request.form.get('event_name')
            self.description = request.form.get('event_description')
            self.toplevel = request_bool(request, 'top_level')
            self.outcome_type = request.form.get('outcome_type')
            self.numeric_range = (
                request_int(request, 'numeric_min', 0),
                request_int(request, 'numeric_max', 1))
            self.selection_strings = request.form.get('selection_strings', "")
            determining_attr_ids = request.form.getlist('determining_attr_id[]')
            changed_attr_ids = request.form.getlist('changed_attr_id[]')
            self.determining_attrs = [
                Attrib(int(attrib_id)) for attrib_id in determining_attr_ids]
            self.changed_attrs = [
                Attrib(int(attrib_id)) for attrib_id in changed_attr_ids]
            self.trigger_chance = (
                request_int(request, 'trigger_numerator', 1),
                request_int(request, 'trigger_denominator', 10))
            self.trigger_by_duration = (
                request.form.get('trigger_timing') == 'during_progress')
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
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")

    def play_by_form(self):
        logger.debug("Saving changes.")
        logger.debug(request.form)
        self.difficulty = request_int(request, 'difficulty')
        self.stat_adjustment = request_int(request, 'stat_adjustment')
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

    def check_trigger(self, elapsed_seconds):
        if self.trigger_by_duration:
            return self.check_trigger_for_duration(elapsed_seconds)
        return self.check_trigger_once()

    def check_trigger_once(self):
        """Returns True if the event triggers."""
        numerator, denominator = self.trigger_chance
        if numerator <= 0:
            return False
        if denominator <= 0:
            raise ValueError("Denominator must be greater than 0")
        probability = numerator / denominator
        random_value = random.random()  # between 0 and 1
        return random_value < probability

    def check_trigger_for_duration(self, elapsed_seconds):
        """Returns True if the event triggers over the given duration."""
        logger.debug("check_trigger_for_duration(%f)", elapsed_seconds)
        numerator, denominator = self.trigger_chance
        if numerator <= 0:
            return False
        if denominator <= 0:
            raise ValueError("Denominator must be greater than 0")
        p_success = numerator / denominator  # success in a single trial
        p_failure = 1 - p_success  # failure in a single trial
        p_overall_failure = p_failure ** elapsed_seconds  # failure over elapsed_seconds trials
        p_overall_success = 1 - p_overall_failure  # at least one success
        random_value = random.random()  # between 0 and 1
        return random_value < p_overall_success
