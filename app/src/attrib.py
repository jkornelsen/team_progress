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
from .db_serializable import Identifiable, coldef

tables_to_create = {
    'attrib': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('desc')}
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
    def __init__(self, id=""):
        super().__init__(id)
        self.name = ""
        self.description = ""

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
        }

    @classmethod
    def from_json(cls, data, _):
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        cls.instances.append(instance)
        return instance

    @classmethod
    def list_from_json(cls, json_data):
        cls.instances.clear()
        for attrib_data in json_data:
            cls.from_json(attrib_data, None)
        return cls.instances

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.instances:
                    self.instances.append(self)
                self.name = request.form.get('attrib_name')
                self.description = request.form.get('attrib_description')
                self.to_db()
            elif 'delete_attrib' in request.form:
                self.instances.remove(self)
                self.remove_from_db(self.id)
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
        else:
            return render_template('configure/attrib.html', current=self)

def set_routes(app):
    @app.route('/configure/attrib/<attrib_id>', methods=['GET', 'POST'])
    def configure_attrib(attrib_id):
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_attrib(): {request.referrer}")
        if attrib_id == "new":
            print("Creating a new attrib.")
            attrib = Attrib()
        else:
            print(f"Retrieving attrib with ID: {attrib_id}")
            attrib = Attrib.get_by_id(int(attrib_id))
        return attrib.configure_by_form()

