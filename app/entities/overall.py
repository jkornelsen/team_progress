from collections import namedtuple
from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for
)

from .item import Item

class Overall():
    """Overall scenario settings such as scenario title and goal,
    and app settings."""
    collection_name = 'overall'

    def __init__(self):
        self.title = "Generic Adventure"
        self.description = (
            "An empty scenario. To begin, go to \"Change Setup\"."
            " You'll probably want to change the"
            " title and this description in addition to doing some basic"
            " setup such as adding a few starting items.")
        self.winning_item = None
        self.winning_quantity = 0

    def to_json(self):
        return {
            'title': self.title,
            'description': self.description,
            'winning_item': self.winning_item.id if self.winning_item else None,
            'winning_quantity': self.winning_quantity
        }

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.title = data['title']
        instance.description = data['description']
        winning_item_id = data['winning_item']
        if winning_item_id is not None:
            instance.winning_item = Item.get_by_id(int(winning_item_id))
            instance.winning_quantity = data['winning_quantity']
        else:
            instance.winning_item = None
            instance.winning_quantity = 0
        return instance

    def to_db(self):
        collection = g.db[self.__class__.collection_name]
        if collection.find_one({'game_token': g.game_token}):
            print(f"updating collection {self.__class__.collection_name}")
            collection.update_one(
                {'game_token': g.game_token}, {'$set': self.to_json()})
        else:
            print(f"inserting collection {self.__class__.collection_name}")
            doc = self.to_json()
            doc ['game_token'] = g.game_token
            collection.insert_one(doc)

    @classmethod
    def from_db(cls):
        print(f"{cls.__name__}.from_db()")
        collection = g.db[cls.collection_name]
        doc = collection.find_one({'game_token': g.game_token})
        if doc is None:
            #return "Overall not found"
            print("doc not found -- returning generic object")
            return cls()
        return cls.from_json(doc)

    @classmethod
    def configure_by_form(cls):
        instance = cls.from_db()
        if request.method == 'POST':
            if 'save_changes' in request.form:
                print("Saving changes.")
                print(request.form)
                instance.title = request.form.get('scenario_title')
                instance.description = request.form.get('scenario_description')
                winning_item_id = request.form.get('winning_item')
                if winning_item_id:
                    instance.winning_item = Item.get_by_id(int(winning_item_id))
                    instance.winning_quantity = int(request.form.get('winning_quantity'))
                else:
                    instance.winning_item = None
                    instance.winning_quantity = 1
                instance.to_db()
            elif 'cancel_changes' in request.form:
                print("Cancelling changes.")
            else:
                print("Neither button was clicked.")
            return redirect(url_for('configure'))
        else:
            return render_template(
                'configure/overall.html',
                current=instance,
                current_user_id=g.user_id)

CharacterRow = namedtuple('CharacterRow',
    ['char_id', 'char_name', 'loc_id', 'loc_name',
    'action_name', 'action_link', 'user_id'])

def get_charlist_display():
    overall = g.game_data.overall
    # Create a list to hold the character rows
    character_rows = []
    for char in overall.game_data.characters:
        if char.toplevel or char.user_id:
            row = CharacterRow(
                char_id=char.id,
                char_name=char.name,
                loc_id=char.location.id if char.location else None,
                loc_name=char.location.name if char.location else None,
                action_name="TODO",
                action_link="TODO",
                user_id=char.user_id
            )
            character_rows.append(row)
    # Add separate rows for each user_id of the same the game token
    # that is not in the character list
    game_token = session.get('game_token')
    user_ids = session.get('game_token_users', {}).get(game_token, [])
    for user_id in user_ids:
        if user_id not in [row.user_id for row in character_rows]:
            row = CharacterRow(
                char_id=None,
                char_name=None,
                loc_id=None,
                loc_name=None,
                action_name=None,
                action_link=None,
                user_id=user_id
            )
            character_rows.append(row)
    # not row.user_id ensures that rows without a user_id (empty or None) come
    # before rows with a user_id.
    # row.user_id or '' handles the case where row.user_id is None or an empty
    # string, ensuring consistent sorting within rows without a user_id.
    # row.char_name is used as the primary sorting criterion, ensuring
    # alphabetical sorting within rows.
    character_rows.sort(key=lambda row:
        (not row.user_id, row.user_id or '', row.char_name))
    return character_rows

def set_routes(app):
    @app.route('/overview')
    def overview():
        #print("session: " +
        #    '\n    '.join([f'{key}: {value}'
        #    for key, value in dict(session).items()]))
        #print("g.game_data: ", vars(g.game_data))
        return render_template(
            'play/overview.html',
            current=g.game_data.overall,
            current_user_id=g.user_id,
            charlist=get_charlist_display())

    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        return Overall.configure_by_form()

