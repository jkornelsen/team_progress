import logging
import random

from flask import g, session, url_for

from .attrib import Attrib, AttribFor
from .character import Character, OwnedItem
from .db_serializable import (
    DbError, DeletionError, CompleteIdentifiable, QueryHelper, coldef)
from .item import Item
from .location import ItemAt, Location
from .user_interaction import MessageLog
from .utils import (
    NumTup, RequestHelper, create_entity, entity_class, format_num)

logger = logging.getLogger(__name__)
tables_to_create = {
    'events': f"""
        {coldef('name')},
        toplevel boolean NOT NULL,
        outcome_type varchar(20) not null,
        trigger_chance real,
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
RELATION_TYPES = ['determining', 'changed', 'triggers']
ENTITY_TYPES = [Attrib, Item, Location]
ENTITY_TYPENAMES = [entity.typename() for entity in ENTITY_TYPES]
OPERATIONS = {
    '+': 'Add',
    '-': 'Subtract',
    '*': 'Multiply',
    '/': 'Divide',
    '^': 'Exponent',
    'log': 'Log (base 10)'
    }

def get_entity_tuple(row):
    """Returns (typename, id).
    Useful when, for example, either attrib_id or item_id may be non-null.
    """
    entity_cls = next(
        (e_cls for e_cls in ENTITY_TYPES
            if getattr(row, e_cls.id_field(), None)), 
        None)
    if entity_cls:
        return (
            entity_cls.typename(),
            getattr(row, entity_cls.id_field())
            )
    return None

def roll_dice(sides):
    return random.randint(1, sides)

class Determinant
    def __init__(self, entity=None):
        self.entity = entity
        self.operation = '+'
        self.label = ""

    def dict_for_json(self):
        return {
            'entity_data': [
                self.entity.typename(),
                self.entity.id
                ],
            'operation': self.operation,
            'label': self.label,
            }

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = cls()
        typename, entity_id = data.get('entity_data', []):
        if typename in ENTITY_TYPENAMES:
            instance.entity = create_entity(typename, entity_id, ENTITY_TYPES)
        instance.operation = data.get('operation', '+')
        instance.label = data.get('label', "")

class Event(CompleteIdentifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = False
        self.outcome_type = OUTCOME_FOURWAY
        self.numeric_range = NumTup((1, 20))  # (min, max)
        self.selection_strings = ""  # newline-separated possible outcomes
        self.determining_entities = []  # Determinant objects
        self.changed_entities = []  # changed by the outcome
        self.triggers_entities = []  # can trigger the event
        self.trigger_chance = 0.0

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'outcome_type': self.outcome_type,
            'selection_strings': self.selection_strings,
            'trigger_chance': self.trigger_chance,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'numeric_range': self.numeric_range.as_list(),
            'determining': [
                det.dict_for_json()
                for det in self.determining_entities]
            })
        for reltype in ('changed', 'triggers'):
            entities = getattr(self, f'{reltype}_entities')
            data.update({
                reltype: [
                    [entity.typename(), entity.id]
                    for entity in entities],
                })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'numeric_range': self.numeric_range,
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
        instance.determining = [
            Determinant.from_data(det_data)
            for det_data in data.get('determining', [])
            ]
        for reltype in ('changed', 'triggers'):
            setattr(
                instance, f'{reltype}_entities', [
                    create_entity(typename, entity_id, ENTITY_TYPES)
                    for typename, entity_id in data.get(reltype, [])
                    if typename in ENTITY_TYPENAMES
                    ]
                )
        instance.trigger_chance = data.get('trigger_chance', 0.1)
        return instance

    def to_db(self):
        logger.debug("to_db()")
        super().to_db()
        for reltype in RELATION_TYPES:
            qhelper = QueryHelper(f"""
                DELETE FROM event_{reltype}
                WHERE event_id = %s AND game_token = %s
                """, [self.id, g.game_token])
            if reltype == 'triggers':
                qhelper.add_limit_expr("AND char_id IS NULL")
            self.execute_change(qhelper=qhelper)
        entity_values = {}  # keyed by entity typename
        for det in self.determining_entities:
            entity = det.entity
            entity_values.setdefault(entity.typename(), []).append((
                g.game_token, self.id, entity.id, det.operation, det.label
                ))
        for typename, values in entity_values.items():
            self.insert_multiple(
                "event_determining",
                f"game_token, event_id, {typename}_id, operation, label",
                values)
        for reltype in ('changed', 'triggers'):
            entity_values = {}  # keyed by entity typename
            for entity in getattr(self, f'{reltype}_entities'):
                entity_values.setdefault(entity.typename(), []).append((
                    g.game_token, self.id, entity.id
                    ))
            for typename, values in entity_values.items():
                self.insert_multiple(
                    f"event_{reltype}",
                    f"game_token, event_id, {typename}_id",
                    values)

    @classmethod
    def load_complete_objects(cls, ids=None):
        logger.debug("load_complete_objects(%s)", ids)
        if cls.empty_values(ids):
            return [cls()]
        qhelper = QueryHelper("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit_in("id", ids)
        rows = cls.execute_select(qhelper=qhelper)
        events = {}  # data (not objects) keyed by ID
        for data in rows:
            events[data.id] = data
        # Get triggers and changed entities
        qhelper_changed = QueryHelper("""
            SELECT *, 'changed' AS reltype
            FROM event_changed
            WHERE game_token = %s
            """, [g.game_token])
        qhelper_changed.add_limit_in("AND event_id", ids)
        qhelper_triggers = QueryHelper("""
            SELECT *, 'triggers' AS reltype
            FROM event_triggers
            WHERE game_token = %s
            """, [g.game_token])
        qhelper_triggers.add_limit_in("AND event_id", ids)
        combined_query = f"""
            ({qhelper_changed.query})
            UNION ALL
            ({qhelper_triggers.query})
            """
        combined_values = qhelper_changed.values + qhelper_triggers.values
        rows = self.execute_select(combined_query, combined_values)
        for row in rows:
            evt = events[data.event_id]
            entity_tup = get_entity_tuple(row)
            if entity_tup:
                evt.setdefault(row.reltype, []).append(entity_tup)
        # Get determinant data
        qhelper = QueryHelper("""
            SELECT *
            FROM event_determining
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit_in("AND event_id", ids)
        rows = cls.execute_select(qhelper=qhelper)
        for row in rows:
            evt = events[data.event_id]
            entity_tup = get_entity_tuple(row)
            if entity_tup:
                row.entity_data = entity_tup
            evt.setdefault('determining', []).append(row)
        # Set list of objects
        instances = {}
        for data in events.values():
            instances[data.id] = cls.from_data(data)
        if ids and any(ids):
            if not instances:
                raise ValueError(f"Could not load events {ids}.")
            setattr(g.active, cls.listname(), instances)
        else:
            g.game_data.set_list(cls, instances.values())
        return instances.values()

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_object(id_to_get)
        # Get all basic data
        g.game_data.from_db_flat([Attrib, Event, Item, Location])
        # Replace partial objects with fully populated objects
        for det in self.determining_entities:
            partial = det.entity
            det.entity = partial.__class__.get_by_id(partial.id)
        for reltype in ('changed', 'triggers'):
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
                req.get_int('numeric_min', 1),
                req.get_int('numeric_max', 20)))
            self.selection_strings = req.get_str('selection_strings', "")
            self.determining_entities = []
            for typename, entity_id, operation, label in zip(
                    req.get_list(f'determining_type[]'),
                    req.get_list(f'determining_id[]'),
                    req.get_list(f'determining_operation[]'),
                    req.get_list(f'determining_label[]')):
                self.determining_entities.append(
                    Determinant.from_data({
                        'entity_data': (typename, entity_id),
                        'operation': operation,
                        'label': label
                    }))
            for reltype in ('changed', 'triggers'):
                setattr(
                    self, f'{reltype}_entities', [
                        create_entity(typename, entity_id, ENTITY_TYPES)
                        for typename, entity_id in zip(
                            req.get_list(f'{reltype}_type[]'),
                            req.get_list(f'{reltype}_id[]'))
                        ]
                    )
            percent_str = req.get_str('trigger_chance', "0%")
            self.trigger_chance = float(percent_str.strip('%')) / 100
            self.to_db()
        elif req.has_key('delete_event'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed event.'
                session['referrer'] = url_for('configure_index')
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")

    def roll_for_outcome(self, die_min, die_max):
        die_higher = max(die_min, die_max)  # for example -2 > -10
        die_lower = min(die_min, die_max)
        if self.outcome_type == OUTCOME_SELECTION:
            strings_list = self.selection_strings.split('\n')
            outcome = random.choice(strings_list)
            display = f"Outcome: {outcome}"
            MessageLog.add(f"{self.name} — {display}")
            return outcome, display
        sides = die_higher - die_lower + 1
        roll = roll_dice(sides)
        outcome_num = die_lower + roll
        if self.outcome_type == OUTCOME_NUMERIC:
            outcome = outcome_num
            display = (
                "{} + {} (1d{})<br>"
                "Outcome = {}"
                ).format(
                    format_num(die_lower),
                    format_num(roll),
                    format_num(sides),
                    format_num(outcome_num),
                    )
        elif self.outcome_type == OUTCOME_FOURWAY:
            major_threshold = round((die_higher - die_lower + 1) * 0.45);
            if outcome_num <= -major_threshold:
                outcome = OUTCOME_CRITICAL_FAILURE
                threshold_display = f"<= {-major_threshold}"
            elif outcome_num <= 0:
                outcome = OUTCOME_MINOR_FAILURE
                threshold_display = f"> {-major_threshold}"
            elif outcome_num < major_threshold:
                outcome = OUTCOME_MINOR_SUCCESS
                threshold_display = f"< {major_threshold}"
            else:
                outcome = OUTCOME_MAJOR_SUCCESS
                threshold_display = f">= {major_threshold}"
            display = (
                "{} + {} (1d{}) = {}<br>"
                "{} {} so Outcome is a {}."
            ).format(
                format_num(die_lower),
                format_num(roll),
                format_num(sides),
                format_num(outcome_num),
                format_num(outcome_num),
                threshold_display,
                OUTCOMES[outcome],
            )
        else:
            raise ValueError(f"Unexpected outcome_type {self.outcome_type}")
        MessageLog.add(f"{self.name} — {display}")
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
        container = container_cls.load_complete_object(container_id)
        if not container:
            raise ValueError(f"{container_cls.readable_type()} not found")
        oldval = ""
        if rel_type == 'attrib':
            rel_dict = container.attribs
            rel_attr = 'val'
        elif container_type in ('char', 'loc'):
            rel_dict = container.items
            rel_attr = 'quantity'
        else:
            rel_dict = {}
        if rel_id in rel_dict:
            rel_obj = rel_dict[rel_id]
            oldval = "from {}".format(format_num(getattr(rel_obj, rel_attr)))
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
        rel_base_obj = rel_obj_cls.load_complete_object(rel_id)
        if not rel_base_obj:
            raise ValueError(f"{rel_obj_cls.readable_type()} not found")
        message = (
            f"Changed {rel_base_obj.name}"
            f" of {container.name} from {oldval} to {newval}")
        session['message'] = message
        MessageLog.add(message)

    def check_trigger(self, trials):
        """Returns True if the event triggers at least once."""
        logger.debug("check_trigger(%f)", trials)
        if self.trigger_chance <= 0:
            return False
        p_failure = 1 - self.trigger_chance  # failure in a single trial
        p_overall_failure = p_failure ** trials
        p_overall_success = 1 - p_overall_failure  # at least one success
        random_value = random.random()  # between 0 and 1
        return random_value < p_overall_success
