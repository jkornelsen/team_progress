import logging

from flask import g, session

from .db_serializable import (
    DbError, DeletionError, Identifiable, QueryHelper, Serializable, coldef)
from .utils import RequestHelper

tables_to_create = {
    'attribs': f"""
        {coldef('name')},
        mult boolean NOT NULL
        """
    }
logger = logging.getLogger(__name__)

class AttribFor(Serializable):
    """Value for attribute of a particular entity,
    or required value of an attribute."""
    def __init__(self, attrib_id=0, val=0):
        self.attrib_id = attrib_id
        self.attrib = None
        self.val = val
        self.entity = None

    @classmethod
    def from_data(cls, data):
        """Can read data from item_attribs, char_attribs,
        or recipe_attribs. (event_attribs doesn't have a value).
        """
        data = cls.prepare_dict(data)
        return cls(
            attrib_id=data.get('attrib_id', 0),
            val=data.get('value', 0.0))

    def as_tuple(self):
        return (self.attrib_id, self.val)

class Attrib(Identifiable):
    """Stat or state or other type of attribute for a character or item.
    Examples: Perception, XP, Max HP, Current HP, Poisoned
    Values of the attrib can be stored as values in attrib dicts of other
    entities.
    """
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.mult = False  # multiplicative or additive

    def _base_export_data(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'mult': self.mult,
            }

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = super().from_data(data)
        instance.mult = data.get('mult', False)
        return instance

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
            FROM {table}
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("id", id_to_get)
        rows = cls.execute_select(qhelper=qhelper)
        instances = []
        for row in rows:
            instances.append(cls.from_data(row))
        if id_to_get:
            return instances[0]
        g.game_data.set_list(cls, instances)
        return instances

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes') or req.has_key('make_duplicate'):
            logger.debug("Saving changes.")
            req.debug()
            self.name = req.get_str('attrib_name')
            self.description = req.get_str('attrib_description')
            self.mult = req.get_bool('multiplicative')
            self.to_db()
        elif req.has_key('delete_attrib'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed attribute.'
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")
