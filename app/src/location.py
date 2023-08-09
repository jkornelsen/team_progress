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
from .db_serializable import (
    Identifiable, MutableNamespace, coldef, new_game_data)
from .item import Item

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
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.destinations = {
            Location(int(dest_id)): distance
            for dest_id, distance in data.get('destinations', {}).items()}
        instance.items = [
            ItemAt.from_json(item_data)
            for item_data in data.get('items', [])]
        return instance

    def json_to_db(self, doc):
        print(f"{self.__class__.__name__}.json_to_db()")
        super().json_to_db(doc)
        for rel_table in ('loc_destinations', 'loc_items'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE loc_id = %s AND game_token = %s
            """, (self.id, self.game_token))
        if doc['destinations']:
            values = [
                (g.game_token, self.id, dest_id, distance)
                for dest_id, distance in doc['destinations'].items()]
            self.insert_multiple(
                "loc_destinations",
                "game_token, loc_id, dest_id, distance",
                values)
        if doc['items']:
            print(f"items: {doc['items']}")
            values = []
            for item_at in doc['items']:
                values.append((
                    g.game_token, self.id,
                    item_at['item_id'],
                    item_at['quantity'],
                    item_at['position']
                    ))
            self.insert_multiple(
                "item_sources",
                "game_token, loc_id, item_id, quantity, position",
                values)

    @classmethod
    def from_db(cls, id_to_get):
        return cls._from_db(id_to_get)

    @classmethod
    def list_from_db(cls):
        return cls._from_db()

    @classmethod
    def _from_db(cls, id_to_get=None):
        print(f"{cls.__name__}._from_db()")
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.loc_id = {tables[0]}.id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            LEFT JOIN {tables[2]}
                ON {tables[2]}.loc_id = {tables[0]}.id
                AND {tables[2]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
        """
        values = [g.game_token]
        if id_to_get:
            query = f"{query}\nAND {{tables[0]}}.id = %s"
            values.append(id_to_get);
        tables_rows = cls.select_tables(
            query, values,
            ['locations', 'loc_destinations', 'loc_items'])
        instances = {}  # keyed by ID
        for loc_data, dest_data, loc_item_data in tables_rows:
            print(f"loc_data {loc_data}")
            print(f"dest_data {dest_data}")
            print(f"loc_item_data {loc_item_data}")
            instance = instances.get(loc_data.id)
            if not instance:
                instance = cls.from_json(vars(loc_data))
                instances[loc_data.id] = instance
            if dest_data.dest_id:
                instance.destinations[dest_data.dest_id] = dest_data.distance
            if loc_item_data.item_id:
                current_data.setdefault(
                    'items', []).append(ItemAt.from_json(loc_item_data))
        # Replace IDs with partial objects
        for instance in instances.values():
            loc_objs = {}
            for dest_id, distance in instance.destinations.items():
                loc_obj = Location(dest_id)
                loc_objs[loc_obj] = distance
            instance.destinations = loc_objs
        # Print debugging info
        print(f"found {len(instances)} locations")
        for instance in instances.values():
            print(f"location {instance.id} ({instance.name})"
                " has {len(instance.destinations)} destinations")
        # Convert and return
        instances = list(instances.values())
        return instances[0] if len(instances) == 1 else instances

    @classmethod
    def list_with_references(cls, json_data=None):
        #if json_data is not None:
        #    super(cls, cls).list_from_json(json_data)
        #else:
        #    instances = super(cls, cls).list_from_db()
        ## replace IDs with actual object referencess now that all entities
        ## have been loaded
        #entity_list = cls.get_list()
        #for instance in entity_list:
        #    instance.destinations = {
        #        cls.get_by_id(destination_id): distance
        #        for destination_id, distance in
        #        id_refs.get('dest', {}).get(instance.id, {}).items()}
        #return entity_list
        raise NotImplementedError()

    @classmethod
    def data_for_configure(cls, config_id):
        print(f"{cls.__name__}.data_for_configure()")
        if config_id == 'new':
            config_id = 0
        else:
            config_id = int(config_id)
        # Get all location data
        locations_data = cls.execute_select("""
            SELECT *
            FROM {table}
            WHERE game_token = %s
        """, (g.game_token,))
        g.game_data.locations = []
        current_data = MutableNamespace()
        for loc_data in locations_data:
            if loc_data.id == config_id:
                current_data = loc_data
            g.game_data.locations.append(Location.from_json(loc_data))
        # Get the current location's destination data
        loc_dest_data = cls.execute_select("""
            SELECT *
            FROM loc_destinations
            WHERE game_token = %s
                AND loc_id = %s
        """, (g.game_token, config_id))
        dests_data = {}
        for row in loc_dest_data:
            dests_data[row.dest_id] = row.distance
        current_data.destinations = dests_data
        # Get all item data and the current location's item relation data
        tables_rows = cls.select_tables("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[0]}.id = {tables[1]}.item_id
                AND {tables[0]}.game_token = {tables[1]}.game_token
                AND {tables[1]}.loc_id = %s
            WHERE {tables[0]}.game_token = %s
        """, (config_id, g.game_token), ['items', 'loc_items'])
        for item_data, loc_item_data in tables_rows:
            if loc_item_data.loc_id:
                current_data.setdefault(
                    'items', []).append(ItemAt.from_json(loc_item_data))
            g.game_data.items.append(Item.from_json(item_data))
        # Create item from data
        current_obj = Location.from_json(current_data)
        # Replace partial objects with fully populated objects
        populated_objs = {}
        for partial_item, distance in current_obj.destinations.items():
            loc = Location.get_by_id(partial_item.id)
            populated_objs[loc] = distance
        current_obj.destinations = populated_objs
        populated_objs = []
        for item_at in current_obj.items:
            partial_item = item_at.item
            item = Item.get_by_id(partial_item.id)
            populated_objs.append(item)
        current_obj.items = populated_objs
        return current_obj

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
                for dest_id, dest_dist in zip(
                        destination_ids, destination_distances):
                    dest_location = Location(int(dest_id))
                    self.destinations[dest_location] = int(dest_dist)
                self.to_db()
            elif 'delete_location' in request.form:
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
        else:
            return render_template(
                'configure/location.html', current=self,
                game=self.game_data)

    def distance(self, other_location):
        return self.destinations.get(other_location, -1)

def set_routes(app):
    @app.route('/configure/location/<location_id>',methods=['GET', 'POST'])
    def configure_location(location_id):
        new_game_data()
        instance = Location.data_for_configure(location_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/location.html',
                current=instance,
                game_data=g.game_data)
        else:
            return instance.configure_by_form()

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

