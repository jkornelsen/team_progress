from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)

class Location:
    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class
    game_data = None

    def __init__(self, new_id='auto'):
        if new_id == 'auto':
            self.__class__.last_id += 1
            self.id = self.__class__.last_id
        else:
            self.id = new_id
        self.name = ""
        self.description = ""
        self.destinations = {}  # keys are Location object, values are distance

    @classmethod
    def get_by_id(cls, id_to_get):
        id_to_get = int(id_to_get)
        return next(
            (instance for instance in cls.instances
            if instance.id == id_to_get), None)

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
    def from_json(cls, data, destination_ids):
        location = cls(int(data['id']))
        location.name = data['name']
        location.description = data.get('description', '')
        destination_ids[location.id] = {
            int(dest_id): distance
            for dest_id, distance in data['destinations'].items()
        }
        cls.instances.append(location)
        return location

    @classmethod
    def list_from_json(cls, json_data):
        cls.instances.clear()
        destination_ids = {}
        for location_data in json_data:
            cls.from_json(location_data, destination_ids)
        cls.last_id = max(
            (location.id for location in cls.instances), default=0)
        # set the destination objects now that all locations have been loaded
        for location in cls.instances:
            location.destinations = {
                cls.get_by_id(location_id): distance
                for location_id, distance in destination_ids.get(
                    location.id, {}).items()
            }
        return cls.instances

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
            elif 'delete_location' in request.form:
                self.__class__.instances.remove(self)
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
                'configure/location.html', current_location=self,
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
            return render_template('play/location.html', location=location, game=Location.game_data)
        else:
            return 'Location not found'

