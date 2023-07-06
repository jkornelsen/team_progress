from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)

class Character:
    last_id = 0  # used to auto-generate a unique id for each object
    instances = []  # all objects of this class
    game_data = None

    def __init__(self, new_id='auto'):
        if new_id == 'auto':
            Character.last_id += 1
            self.id = Character.last_id
        else:
            self.id = new_id
        self.name = ""
        self.description = ""
        self.character_type = ""  # NPC or for a particular player
        self.attributes = []  # list of Attribute objects
        self.items = {}  # keys are Item object, values are slot name
        self.location = None  # Location object

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
            'character_type': self.character_type,
            'items': [item.to_json() for item in self.items],
            'location_id': self.location.id if self.location else None
        }

    @classmethod
    def from_json(cls, data):
        character = cls(int(data['id']))
        character.name = data['name']
        character.description = data.get('description', '')
        character.character_type = data['character_type']
        character.items = [Item.get_by_id(item_data['id']) for item_data in data['items']]
        character.location = Location.get_by_id(data['location_id'])
        cls.instances.append(character)
        return character

    @classmethod
    def character_list_from_json(cls, json_data):
        cls.instances.clear()
        for character_data in json_data:
            cls.from_json(character_data)
        cls.last_id = max(character.id for character in cls.instances)
        return cls.instances

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
                self.name = request.form.get('character_name')
                self.description = request.form.get('character_description')
                self.character_type = request.form.get('character_type')
                item_ids = request.form.getlist('item_id[]')
                item_worn = request.form.getlist('item_worn[]')
                self.items = []
                for item_id, worn in zip(item_ids, item_worn):
                    item = Item.get_by_id(int(item_id))
                    if item:
                        item.worn = (worn == item_id)
                        self.items.append(item)
                location_id = request.form.get('character_location')
                self.location = Location.get_by_id(int(location_id)) if location_id else None
                print(request.form)
            elif 'delete_character' inrequest.form:
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
                'configure/character.html', current_character=self,
                game=self.__class__.game_data)

def set_routes(app):
    @app.route('/configure/character/<character_id>', methods=['GET', 'POST'])
    def configure_character(character_id):
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_character(): {request.referrer}")
        if character_id == "new":
            print("Creating a new character.")
            character = Character()
        else:
            print(f"Retrieving character with ID: {character_id}")
            character = Character.get_by_id(int(character_id))
        return character.configure_by_form()

    @app.route('/play/character/<int:character_id>')
    def play_character(character_id):
        character = Character.get_by_id(character_id)
        if character:
            return render_template('play/character.html', character=character, game=Character.game_data)
        else:
            return 'Character not found'

