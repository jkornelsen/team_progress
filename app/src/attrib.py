from flask import g, request, session
from .db_serializable import Identifiable, coldef

tables_to_create = {
    'attribs': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')}
""",
    # example "{10: 'Very Hungry', 50: 'Full'}
    # threshold_names JSON NOT NULL
}

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
    def from_json(cls, data, _=None):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        return instance

    @classmethod
    def list_from_json(cls, json_data):
        print(f"{cls.__name__}.list_from_json()")
        instances = []
        for attrib_data in json_data:
            instances.append(cls.from_json(attrib_data, None))
        return instances

    @classmethod
    def data_for_file(cls):
        print(f"{cls.__name__}.data_for_file()")
        data = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
        """, (g.game_token,))
        instances = [cls.from_json(vars(dat)) for dat in data]
        return instances

    @classmethod
    def data_for_configure(cls, id_to_get):
        print(f"{cls.__name__}.data_for_configure()")
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        data = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
                AND id = %s
        """, (g.game_token, id_to_get), fetch_all=False)
        instance = cls.from_json(vars(data))
        return instance

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            self.name = request.form.get('attrib_name')
            self.description = request.form.get('attrib_description')
            self.to_db()
        elif 'delete_attrib' in request.form:
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed attribute.'
            except DbError as e:
                raise DeletionError(e)
        elif 'cancel_changes' in request.form:
            print("Cancelling changes.")
        else:
            print("Neither button was clicked.")
