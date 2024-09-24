import logging
import random

from flask import g, session

from .attrib import Attrib
from .character import Character
from .db_serializable import (
    DbError, DeletionError, Identifiable, QueryHelper, coldef)
from .item import Item
from .location import Location
from .utils import NumTup, RequestHelper

OUTCOME_TYPES = [
    'fourway',  # critical/minor failure or success
    'numeric',  # such as a damage number
    'selection'  # random selection from a list
    ]
(OUTCOME_FOURWAY,
    OUTCOME_NUMERIC,
    OUTCOME_SELECTION) = OUTCOME_TYPES
OUTCOMES = [
    "Critical Failure",
    "Minor Failure",
    "Minor Success",
    "Major Success"
    ]
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
        return Item(entity_id)
    if entity_name == 'location':
        return Location(entity_id)
    raise ValueError(f"Unknown entity_name: {entity_name}")

tables_to_create = {
    'events': f"""
        {coldef('name')},
        toplevel boolean NOT NULL,
        outcome_type varchar(20) not null,
        trigger_chance integer[2],
        trigger_by_duration boolean,
        numeric_range integer[2],
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
        self.numeric_range = NumTup((0, 10))  # (min, max)
        self.selection_strings = ""  # newline-separated possible outcomes
        self.determining_attrs = []  # Attrib objects determining the outcome
        self.changed_attrs = []  # Attrib objects changed by the outcome
        self.trigger_chance = NumTup((0, 1))  # (numerator, denominator)
        self.trigger_by_duration = True  # during progress or when finished
        self.triggers = []  # Item or Location objects that can trigger
        ## For a particular occurrence, not stored in Event table
        self.difficulty = 10  # Moderate
        self.stat_adjustment = 0  # for example, 5 for perception
        self.advantage = 0  # for example +1 means best of two rolls
        self.outcome = 0

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'outcome_type': self.outcome_type,
            'selection_strings': self.selection_strings,
            'trigger_by_duration': self.trigger_by_duration,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'determining_attrs': [
                attrib.id for attrib in self.determining_attrs],
            'changed_attrs': [
                attrib.id for attrib in self.changed_attrs],
            'triggers': [
                (entity.basename(), entity.id)
                for entity in self.triggers],
            'numeric_range': self.numeric_range.as_list(),
            'trigger_chance': self.trigger_chance.as_list(),
            })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'numeric_range': self.numeric_range,
            'trigger_chance': self.trigger_chance,
            })
        return data

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = super().from_data(data)
        instance.toplevel = data.get('toplevel', True)
        instance.outcome_type = data.get('outcome_type', OUTCOME_FOURWAY)
        instance.numeric_range = NumTup(data.get('numeric_range', (0, 10)))
        instance.selection_strings = data.get('selection_strings', "")
        instance.determining_attrs = [
            Attrib(int(attrib_id))
            for attrib_id in data.get('determining_attrs', [])]
        instance.changed_attrs = [
            Attrib(int(attrib_id))
            for attrib_id in data.get('changed_attrs', [])]
        instance.trigger_chance = NumTup(data.get('trigger_chance', (0, 1)))
        instance.trigger_by_duration = data.get('trigger_by_duration', True)
        instance.triggers = [
            create_trigger_entity(entity_name, entity_id)
            for entity_name, entity_id in data.get('triggers', [])]
        return instance

    def to_db(self):
        logger.debug("to_db()")
        super().to_db()
        for rel_table in ('event_attribs', 'event_triggers'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE event_id = %s AND game_token = %s
                """, (self.id, self.game_token))
        values = []
        for determining in ('determining', 'changed'):
            attribs = getattr(self, f'{determining}_attrs')
            logger.debug("%s_attrs=%s", determining, attribs)
            for attrib in attribs:
                values.append((
                    g.game_token, self.id, attrib.id,
                    determining == 'determining'
                    ))
        if values:
            self.insert_multiple(
                "event_attribs",
                "game_token, event_id, attrib_id, determining",
                values)
        if self.triggers:
            logger.debug("triggers: %s", self.triggers)
            values = []
            for entity in self.triggers:
                item_id = entity.id if isinstance(entity, Item) else None
                loc_id = entity.id if isinstance(entity, Location) else None
                values.append((g.game_token, self.id, item_id, loc_id))
            self.insert_multiple(
                "event_triggers",
                "game_token, event_id, item_id, loc_id",
                values)

    @classmethod
    def load_complete_objects(cls, id_to_get=None):
        """Load objects with everything needed for storing to db
        or JSON file.
        :param id_to_get: specify to only load a single object
        """
        logger.debug("load_complete_objects(%s)", id_to_get)
        if id_to_get in ['new', '0', 0]:
            return cls()
        # Get event base data
        # TODO: Use select_tables to left join on event_attribs data,
        # because it won't multiply rows.
        # That way only 2 instead of 3 queries are needed.
        qhelper = QueryHelper("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("id", id_to_get)
        events = {}  # data (not objects) keyed by ID
        event_rows = cls.execute_select(qhelper=qhelper)
        for row in event_rows:
            events[row.id] = row
        # Get attrib relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM event_attribs
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("event_id", id_to_get)
        attrib_rows = cls.execute_select(qhelper=qhelper)
        for row in attrib_rows:
            evt = events[row.event_id]
            if row.determining:
                listname = 'determining_attrs'
            else:
                listname = 'changed_attrs'
            evt.setdefault(listname, []).append(row.attrib_id)
        # Get trigger relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM event_triggers
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("event_id", id_to_get)
        trigger_rows = cls.execute_select(qhelper=qhelper)
        for row in trigger_rows:
            evt = events[row.event_id]
            if row.item_id:
                trigger_tup = (Item.basename(), row.item_id)
            else:
                trigger_tup = (Location.basename(), row.loc_id)
            evt.setdefault('triggers', []).append(trigger_tup)
        # Set list of objects
        instances = []
        for data in events.values():
            instances.append(cls.from_data(data))
        if id_to_get:
            return instances[0]
        g.game_data.set_list(cls, instances)
        return instances

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_objects(id_to_get)
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
            trigger_entity_class = partial_obj.__class__
            populated_objs.append(
                trigger_entity_class.get_by_id(partial_obj.id))
        current_obj.triggers = populated_objs
        # Print debugging info
        logger.debug("found %d triggers", len(current_obj.triggers))
        logger.debug("found %d det att", len(current_obj.determining_attrs))
        if current_obj.triggers:
            trigger = current_obj.triggers[0]
            logger.debug("type=%s", trigger.__class__.__name__)
            logger.debug("id=%d", trigger.id)
            logger.debug("name=%s", trigger.name)
        return current_obj

    @classmethod
    def data_for_play(cls, id_to_get):
        logger.debug("data_for_play()")
        current_obj = cls.data_for_configure(id_to_get)
        Character.load_characters_for_event(id_to_get)
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
            g.game_data.events.append(Event.from_data(event_row))
        return g.game_data.events

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes') or req.has_key('make_duplicate'):
            req.debug()
            self.name = req.get_str('event_name')
            self.description = req.get_str('event_description')
            req = RequestHelper('form')
            self.toplevel = req.get_bool('top_level')
            self.outcome_type = req.get_str('outcome_type')
            self.numeric_range = NumTup((
                req.get_int('numeric_min', 0),
                req.get_int('numeric_max', 1)))
            self.selection_strings = req.get_str('selection_strings', "")
            determining_attr_ids = req.get_list('determining_attr_id[]')
            changed_attr_ids = req.get_list('changed_attr_id[]')
            self.determining_attrs = [
                Attrib(int(attrib_id)) for attrib_id in determining_attr_ids]
            self.changed_attrs = [
                Attrib(int(attrib_id)) for attrib_id in changed_attr_ids]
            self.trigger_chance = NumTup((
                req.get_int('trigger_numerator', 1),
                req.get_int('trigger_denominator', 10)))
            self.trigger_by_duration = (
                req.get_str('trigger_timing') == 'during_progress')
            trigger_types = req.get_list('entity_type[]')
            trigger_ids = req.get_list('entity_id[]')
            self.triggers = [
                create_trigger_entity(entity_name, entity_id)
                for entity_name, entity_id in zip(trigger_types, trigger_ids)]
            self.to_db()
        elif req.has_key('delete_event'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed event.'
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")

    def play_by_form(self):
        logger.debug("Saving changes.")
        req = RequestHelper('form')
        req.debug()
        self.difficulty = req.get_int('difficulty')
        self.stat_adjustment = req.get_int('stat_adjustment')
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
            sign = 1 if range_max >= 0 else -1
            sides = range_max - range_min
            roll = roll_dice(sides)
            self.outcome = range_min + roll + self.stat_adjustment
            display = (
                "Min ({}) + 1d{} ({}) + Stat Adjustment ({})<br>"
                "Outcome = {}"
            ).format(
                range_min,
                sides,
                roll,
                self.stat_adjustment,
                self.outcome
            )
        elif self.outcome_type == OUTCOME_SELECTION:
            strings_list = self.selection_strings.split('\n')
            random_string = random.choice(strings_list)
            display = f"Outcome: {random_string}"
        else:
            raise ValueError(f"Unexpected outcome_type {self.outcome_type}")
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
