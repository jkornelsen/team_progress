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
from .db_serializable import Identifiable, coldef, new_game_data

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
    def data_for_configure(cls, config_id):
        print(f"{cls.__name__}.data_for_configure()")
        if config_id == 'new':
            config_id = 0
        else:
            config_id = int(config_id)
        return cls.from_db(config_id)

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            self.name = request.form.get('attrib_name')
            self.description = request.form.get('attrib_description')
            self.to_db()
        elif 'delete_attrib' in request.form:
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

def set_routes(app):
    @app.route('/configure/attrib/<attrib_id>', methods=['GET', 'POST'])
    def configure_attrib(attrib_id):
        new_game_data()
        instance = Attrib.data_for_configure(attrib_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/attrib.html',
                current=instance,
                game_data=g.game_data)
        else:
            return instance.configure_by_form()

