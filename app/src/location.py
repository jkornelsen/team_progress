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
    'locations': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        dimensions integer[2]
    """
}

class Location(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.destinations = {}  # Location objects and their distance
        self.items = {}  # Item objects with their quantity and position
        self.characters = {}  # Character objects and their position

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'destinations': {
                str(dest.id): distance
                for dest, distance in self.destinations.items()
            }
        }

    @classmethod
    def from_json(cls, data, id_refs=None):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        if id_refs is not None:
            id_refs.setdefault('dest', {})[instance.id] = {
                int(dest_id): distance
                for dest_id, distance in data['destinations'].items()
            }
        return instance

    @classmethod
    def list_with_references(cls, json_data=None):
        if json_data:
            super(cls, cls).list_from_json(json_data)
        else:
            instances = super(cls, cls).list_from_db()
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        entity_list = cls.get_list()
        for instance in entity_list:
            instance.destinations = {
                cls.get_by_id(destination_id): distance
                for destination_id, distance in
                id_refs.get('dest', {}).get(instance.id, {}).items()}
        return entity_list

    @classmethod
    def list_from_json(cls, json_data):
        return cls.list_with_references(json_data)

    @classmethod
    def list_from_db(cls):
        return cls.list_with_references()

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                entity_list = self.get_list()
                if self not in entity_list:
                    entity_list.append(self)
                self.name = request.form.get('location_name')
                self.description = request.form.get('location_description')
                destination_ids = request.form.getlist('destination_id[]')
                destination_distances = request.form.getlist('destination_distance[]')
                self.destinations = {}
                for dest_id, dest_dist in zip(destination_ids, destination_distances):
                    dest_location = Location.get_by_id(int(dest_id))
                    if dest_location:
                        self.destinations[dest_location] = int(dest_dist)
                self.to_db()
            elif 'delete_location' in request.form:
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
            return render_template(
                'configure/location.html', current=self,
                game=self.game_data)

    def distance(self, other_location):
        return self.destinations.get(other_location, -1)

def set_routes(app):
    @app.route('/configure/location/<location_id>',methods=['GET', 'POST'])
    def configure_location(location_id):
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_location(): {request.referrer}")
        if location_id == "new":
            print("Creating a new location.")
            location = Location()
        else:
            print(f"Retrieving location with ID: {location_id}")
            location = Location.get_by_id(int(location_id))
        return location.configure_by_form()

    @app.route('/play/location/<int:location_id>')
    def play_location(location_id):
        location = Location.get_by_id(location_id)
        if location:
            return render_template(
                'play/location.html',
                current=location,
                game_data=g.game_data)
        else:
            return 'Location not found'

