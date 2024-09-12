from flask import g, session
import logging

from .db_serializable import Identifiable, coldef
from .utils import RequestHelper

tables_to_create = {
    'attribs': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')}
""",
    # example "{10: 'Very Hungry', 50: 'Full'}
    # threshold_names JSON NOT NULL
}
logger = logging.getLogger(__name__)

class AttribOf:
    """Attribute of Item or Character class object."""
    def __init__(self, attrib=None, attrib_id=None, val=0):
        if attrib is not None:
            self.attrib = attrib
        elif attrib_id is not None:
            self.attrib = Attrib(attrib_id)
        else:
            self.attrib = Attrib()
        self.val = val

    @classmethod
    def from_json(cls, data):
        return cls(
            attrib_id=data.get('attrib_id', 0),
            val=data.get('value', 0.0))

class AttribReq:
    """For example the attribute value required to produce an item."""
    def __init__(self, attrib_id=0, val=0):
        self.attrib = Attrib(attrib_id)
        self.val = val
        self.entity = None  # entity that fulfills the requirement

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

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        return instance

    @classmethod
    def list_from_json(cls, json_data):
        logger.debug("list_from_json()")
        instances = []
        for attrib_data in json_data:
            instances.append(cls.from_json(attrib_data))
        return instances

    @classmethod
    def load_complete_object(cls, id_to_get):
        logger.debug("load_complete_object(%s)", id_to_get)
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        row = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
                AND id = %s
        """, (g.game_token, id_to_get), fetch_all=False)
        instance = cls.from_json(vars(row))
        return instance

    @classmethod
    def load_complete_objects(cls):
        logger.debug("load_complete_objects()")
        rows = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
        """, (g.game_token,))
        # Create objects from data
        g.game_data.set_list(cls, 
            [cls.from_json(vars(row)) for row in rows])
        return g.game_data.get_list(cls)

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes'):  # button was clicked
            logger.debug("Saving changes.")
            req.debug()
            self.name = req.get_str('attrib_name')
            self.description = req.get_str('attrib_description')
            self.to_db()
        elif req.has_key('delete_attrib'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed attribute.'
            except DbError as e:
                raise DeletionError(e)
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")
