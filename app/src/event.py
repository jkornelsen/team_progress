import logging
import random

from flask import g, session

from .attrib import Attrib, AttribFor
from .character import Character, OwnedItem
from .db_serializable import (
    DbError, DeletionError, Identifiable, QueryHelper, coldef)
from .item import Item
from .location import ItemAt, Location
from .utils import (
    NumTup, RequestHelper, create_entity, entity_class, format_num)

logger = logging.getLogger(__name__)
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
RELATION_TYPES = ['determining', 'changed', 'triggers']
ENTITY_TYPES = [Attrib, Item, Location]
ENTITY_TYPENAMES = [entity.typename for entity in ENTITY_TYPES]

def roll_dice(sides):
    return random.randint(1, sides)

class Event(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = True
        self.outcome_type = OUTCOME_FOURWAY
        self.numeric_range = NumTup((0, 10))  # (min, max)
        self.selection_strings = ""  # newline-separated possible outcomes
        self.determining_entities = []  # these determine the outcome
        self.changed_entities = []  # changed by the outcome
        self.triggers_entities = []  # can trigger the event
        self.trigger_chance = NumTup((0, 1))  # (numerator, denominator)
        self.trigger_by_duration = True  # during progress or when finished

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
            'numeric_range': self.numeric_range.as_list(),
            'trigger_chance': self.trigger_chance.as_list(),
            })
        for reltype in RELATION_TYPES:
            entities = getattr(self, f'{reltype}_entities')
            data.update({
                reltype: [
                    [entity.typename, entity.id]
                    for entity in entities],
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
        instance.numeric_range = NumTup(data.get('numeric_range') or (0, 10))
        instance.selection_strings = data.get('selection_strings', "")
        for reltype in RELATION_TYPES:
            setattr(
                instance, f'{reltype}_entities', [
                    create_entity(typename, entity_id, ENTITY_TYPES)
                    for typename, entity_id in data.get(reltype, [])
                    if typename in ENTITY_TYPENAMES
                    ]
                )
        instance.trigger_chance = NumTup(data.get('trigger_chance') or (0, 1))
        instance.trigger_by_duration = data.get('trigger_by_duration', True)
        return instance

    def to_db(self):
        logger.debug("to_db()")
        super().to_db()
        self.execute_change("""
            DELETE FROM event_entities
            WHERE event_id = %s AND game_token = %s
            """, (self.id, g.game_token))
        entity_values = {}  # keyed by entity typename
        for reltype in RELATION_TYPES:
            entities = getattr(self, f'{reltype}_entities')
            for entity in entities:
                entity_values.setdefault(entity.typename, []).append((
                    g.game_token, self.id, entity.id, reltype
                    ))
        for typename, values in entity_values.items():
            self.insert_multiple(
                "event_entities",
                f"game_token, event_id, {typename}_id, reltype",
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
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.event_id = {tables[0]}.id
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit("{tables[0]}.id", id_to_get)
        rows = cls.select_tables(
            qhelper=qhelper, tables=['events', 'event_entities'])
        events = {}  # data (not objects) keyed by ID
        for base_row, entities_row in rows:
            evt = events.setdefault(base_row.id, base_row)
            entity_cls = next(
                (e_cls for e_cls in ENTITY_TYPES
                if getattr(entities_row, e_cls.id_field)), None
                )
            if entity_cls:
                entity_tup = (
                    entity_cls.typename,
                    getattr(entities_row, entity_cls.id_field)
                    )
                evt.setdefault(entities_row.reltype, []).append(entity_tup)
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
        for reltype in RELATION_TYPES:
            listname = f'{reltype}_entities'
            setattr(
                current_obj, listname, [
                    partial.__class__.get_by_id(partial.id)
                    for partial in getattr(current_obj, listname)
                    ]
                )
        return current_obj

    @classmethod
    def load_triggers_for_type(cls, id_to_get, typename):
        logger.debug("load_triggers_for_type()")
        qhelper = QueryHelper("""
            SELECT {tables[0]}.*
            FROM {tables[1]}
            INNER JOIN {tables[0]}
                ON {tables[0]}.id = {tables[1]}.event_id
                AND {tables[0]}.game_token = {tables[1]}.game_token
            WHERE {tables[1]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit(f"{{tables[1]}}.{typename}_id", id_to_get)
        rows = cls.execute_select(
            qhelper=qhelper, tables=['events', 'event_entities'])
        g.game_data.events = []
        for row in rows:
            g.game_data.events.append(Event.from_data(row))
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
                req.get_int('numeric_max', 10)))
            self.selection_strings = req.get_str('selection_strings', "")
            for reltype in RELATION_TYPES:
                setattr(
                    self, f'{reltype}_entities', [
                        create_entity(typename, entity_id, ENTITY_TYPES)
                        for typename, entity_id in zip(
                            req.get_list(f'{reltype}_type[]'),
                            req.get_list(f'{reltype}_id[]'))
                        ]
                    )
            self.trigger_chance = NumTup((
                req.get_int('trigger_numerator', 0),
                req.get_int('trigger_denominator', 1)))
            self.trigger_by_duration = (
                req.get_str('trigger_timing') == 'during_progress')
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

    def roll_for_outcome(self, difficulty, stat_adjustment):
        if self.outcome_type == OUTCOME_FOURWAY:
            roll = roll_dice(20)
            total = roll + stat_adjustment - difficulty
            if total <= -OUTCOME_MARGIN:
                outcome = OUTCOME_CRITICAL_FAILURE
            elif total <= 0:
                outcome = OUTCOME_MINOR_FAILURE
            elif total < OUTCOME_MARGIN:
                outcome = OUTCOME_MINOR_SUCCESS
            else:
                outcome = OUTCOME_MAJOR_SUCCESS
            display = (
                "1d20 ({}) + Stat Adjustment {} - Difficulty {} = {}<br>"
                "Outcome is a {}."
            ).format(
                roll,
                format_num(stat_adjustment),
                difficulty,
                format_num(total),
                OUTCOMES[outcome],
            )
        elif self.outcome_type == OUTCOME_NUMERIC:
            range_min, range_max = self.numeric_range
            sides = range_max - range_min
            roll = roll_dice(sides)
            outcome = range_min + roll + stat_adjustment
            display = (
                "Min ({}) + 1d{} ({}) + Stat Adjustment ({})<br>"
                "Outcome = {}"
            ).format(
                format_num(range_min),
                format_num(sides),
                format_num(roll),
                format_num(stat_adjustment),
                format_num(outcome),
            )
        elif self.outcome_type == OUTCOME_SELECTION:
            strings_list = self.selection_strings.split('\n')
            random_string = random.choice(strings_list)
            display = f"Outcome: {random_string}"
        else:
            raise ValueError(f"Unexpected outcome_type {self.outcome_type}")
        return outcome, display

    @classmethod
    def change_by_form(cls):
        req = RequestHelper('form')
        req.debug()
        if not req.has_key('change_entity'):
            raise ValueError("Unrecognized form submission.")
        rel_id = req.get_int('key_id', 0)
        rel_type = req.get_str('key_type', '')
        container_id = req.get_int('container_id', 0)
        container_type = req.get_str('container_type', '')
        newval = req.get_str('newval', "0")
        container_cls = entity_class(
            container_type, [Character, Item, Location])
        container = container_cls.load_complete_objects(container_id)
        if not container:
            raise ValueError(f"{container_cls.readable_type} not found")
        oldval = ""
        if rel_type == 'attrib':
            rel_dict = container.attribs
            rel_attr = 'val'
        else:
            rel_dict = container.items
            rel_attr = 'quantity'
        if rel_id in rel_dict:
            rel_obj = rel_dict[rel_id]
            oldval = "from " + format_num(getattr(rel_obj, rel_attr))
            setattr(rel_obj, rel_attr, newval)
        else:
            if rel_type == 'attrib':
                rel_dict[rel_id] = AttribFor(rel_id, newval)
            elif rel_type == 'item':
                if container_type == 'char':
                    rel_dict[rel_id] = OwnedItem.from_data({
                        'item_id': rel_id,
                        'quantity': newval
                        })
                elif container_type == 'loc':
                    rel_dict[rel_id] = ItemAt.from_data({
                        'item_id': rel_id,
                        'quantity': newval
                        })
                elif container_type == 'item':
                    container.pile.quantity = newval
                else:
                    raise ValueError(
                        f"Unexpected container type '{container_type}'.")
            else:
                raise ValueError(f"Unexpected rel type '{rel_type}'.")
        container.to_db()
        rel_obj_cls = entity_class(rel_type, [Attrib, Item])
        rel_base_obj = rel_obj_cls.load_complete_objects(rel_id)
        if not rel_base_obj:
            raise ValueError(f"{rel_obj_cls.readable_type} not found")
        session['message'] = (
            f"Changed {rel_base_obj.name}"
            f" of {container.name} from {oldval} to {newval}")

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
