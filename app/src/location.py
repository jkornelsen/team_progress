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
from .db_serializable import DbSerializable

class Location(DbSerializable):
    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class

    def __init__(self, new_id='auto'):
        if new_id == 'auto':
            self.__class__.last_id += 1
            self.id = self.__class__.last_id
        else:
            self.id = new_id
        self.name = ""
        self.description = ""
        self.destinations = {}  # keys are Location object, values are distance
        self.items = []  # list of Item objects currently at this location

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'destinations': {
                dest.id: distance
                for dest, distance in self.destinations.items()
            }
        }

    @classmethod
    def from_json(cls, data, id_refs):
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        id_refs.setdefault('dest', {})[instance.id] = {
            int(dest_id): distance
            for dest_id, distance in data['destinations'].items()
        }
        cls.instances.append(instance)
        return instance

    @classmethod
    def list_with_references(cls, callback):
        id_refs = {}
        callback(id_refs)
        # replace IDs with actual object referencess now that all entities
        # have been loaded
        for instance in cls.instances:
            instance.destinations = {
                cls.get_by_id(destination_id): distance
                for destination_id, distance in
                id_refs.get('dest', {}).get(instance.id, {}).items()}
            instance.progress.destinations = instance.destinations
        return cls.instances

    @classmethod
    def list_from_json(cls, json_data):
        def callback(id_refs):
            super(cls, cls).list_from_json(json_data, id_refs)
        return cls.list_with_references(callback)

    @classmethod
    def list_from_db(cls):
        def callback(id_refs):
            super(cls, cls).list_from_db(id_refs)
        return cls.list_with_references(callback)

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
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
                self.__class__.instances.remove(self)
                self.__class__.remove_from_db(self.id)
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
                game=self.__class__.game_data)

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
                current_user_id=g.user_id,
                game_data=g.game_data)
        else:
            return 'Location not found'

