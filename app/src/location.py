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
    'locations': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        dimensions integer[2]
    """
}

class ItemAt:
    def __init__(self, item=None):
        self.item = item
        if not item:
            self.item = Item()
        self.quantity = 0
        self.position = (0, 0)

    def to_json(self):
        return {
            'item_id': self.item.id,
            'quantity': self.quantity,
            'position': self.position,
        }

    @classmethod
    def from_json(cls, item):
        instance = cls(Item(int(data.get('item_id', 0))))
        instance.quantity = data['quantity']
        instance.position = data['position']
        return instance

class Location(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.destinations = {}  # Location objects and their distance
        self.items = []  # ItemAt objects

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'destinations': {
                str(dest.id): distance
                for dest, distance in self.destinations.items()},
            'items': [
                item_at.to_json()
                for item_at in self.items],
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data['id']))
        instance.name = data['name']
        instance.description = data.get('description', '')
        instance.destinations = {
            int(dest_id): distance
            for dest_id, distance in data['destinations'].items()}
        instance.items = [
            ItemAt.from_json(item_data)
            for item_data in data['items']]
        return instance

    #@classmethod
    #def list_with_references(cls, json_data=None):
    #    if json_data is not None:
    #        super(cls, cls).list_from_json(json_data)
    #    else:
    #        instances = super(cls, cls).list_from_db()
    #    # replace IDs with actual object referencess now that all entities
    #    # have been loaded
    #    entity_list = cls.get_list()
    #    for instance in entity_list:
    #        instance.destinations = {
    #            cls.get_by_id(destination_id): distance
    #            for destination_id, distance in
    #            id_refs.get('dest', {}).get(instance.id, {}).items()}
    #    return entity_list

    #@classmethod
    #def list_from_json(cls, json_data):
    #    return cls.list_with_references(json_data)

    #@classmethod
    #def list_from_db(cls):
    #    return cls.list_with_references()

    @classmethod
    def data_for_configure(cls, config_id):
        print(f"{cls.__name__}.data_for_configure()")
        if config_id == 'new':
            config_id = 0
        else:
            config_id = int(config_id)
        # Get all location data
        locations_data = DbSerializable.execute_select("""
            SELECT *
            FROM {cls.tablename()}
            WHERE game_token = %s
        """, (g.game_token,))
        g.game_data.locations = []
        current_data = MutableNamespace()
        for loc_data in locations_data:
            if loc_data.id == config_id:
                current_data = loc_data
            g.game_data.locations.append(Location.from_json(loc_data))
        # Get the current location's destination data
        loc_dest_data = DbSerializable.execute_select("""
            SELECT *
            FROM location_destinations
            WHERE game_token = %s
                AND origin_id = %s
        """, (g.game_token, config_id))
        dests_data = {}
        for row in loc_dest_data:
            dests_data[row.dest_id] = row.distance
        current_data.destinations = dests_data
        # Get all item data and the current location's item relation data
        tables_rows = DbSerializable.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[0]}.id = {tables[1]}.item_id
                AND {tables[0]}.game_token = {tables[1]}.game_token
                AND {tables[1]}.location_id = %s
            WHERE {tables[0]}.game_token = %s
        """, (config_id, g.game_token), ['items', 'loc_items'])
        for item_data, loc_item_data in tables_rows:
            if loc_item_data.location_id:
                current_data.setdefault(
                    'items', []).append(ItemAt.from_json(loc_item_data))
            g.game_data.items.append(Item.from_json(item_data))
        # Create item from data
        current_loc = Location.from_json(current_data)
        # Replace partial objects with fully populated objects
        populated_objs = []
        for item_at in current_loc.items:
            partial_item = item_at.item
            item = Item.get_by_id(partial_item.id)
            populated_objs.append(item)
        current_loc.items = populated_objs
        return current_loc

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
            new_game_data()
            instance = Location.data_for_configure(location_id)
            return render_template(
                'configure/location.html',
                current=instance,
                game_data=g.game_data)
        else:
            return Location().configure_by_form()

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

