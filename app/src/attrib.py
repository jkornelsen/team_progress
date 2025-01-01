import logging

from flask import g, session, url_for

from .db_serializable import (
    DbError, DeletionError, CompleteIdentifiable, QueryHelper, Serializable,
    coldef)
from .utils import RequestHelper

logger = logging.getLogger(__name__)
tables_to_create = {
    'attribs': f"""
        {coldef('name')},
        enum_list TEXT[],
        is_binary boolean NOT NULL
        """
    }

class AttribFor(Serializable):
    """Value of attribute for a particular entity (the subject)."""
    def __init__(self, attrib_id=0, val=0):
        self.attrib_id = attrib_id
        self.attrib = None
        self.val = val
        self.subject = None  # Item or Character that the value is for

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        return cls(
            attrib_id=data.get('attrib_id', 0),
            val=data.get('value', 0.0))

    def as_tuple(self):
        return (self.attrib_id, self.val)

class Attrib(CompleteIdentifiable):
    """Stat or state or other type of attribute for a character or item.
    Examples: Perception, XP, Max HP, Current HP, Poisoned
    Values of the attrib can be stored as values in attrib dicts of other
    entities.
    """
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.binary = False  # true/false or number
        self.enum = []  # list of strings to set numerical attrib value

    def _base_export_data(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'is_binary': self.binary,
            'enum_list': self.enum,
            }

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = super().from_data(data)
        instance.binary = data.get('is_binary', False)
        instance.enum = list(data.get('enum_list') or [])
        return instance

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
        instances = {}
        for data in rows:
            instances[data.id] = cls.from_data(data)
        if ids and any(ids) and not instances:
            logger.warning(f"Could not load attributes {ids}.")
        cls.get_coll().primary.update(instances)
        return instances.values()

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes') or req.has_key('make_duplicate'):
            logger.debug("Saving changes.")
            req.debug()
            self.name = req.get_str('attrib_name')
            self.description = req.get_str('attrib_description')
            self.enum = [
                line.strip()
                for line in req.get_str('enum').splitlines()
                if line.strip()]
            self.binary = (
                req.get_str('value_type') == 'binary' and not self.enum)
            self.to_db()
        elif req.has_key('delete_attrib'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed attribute.'
                session['referrer'] = url_for('configure_index')
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")
