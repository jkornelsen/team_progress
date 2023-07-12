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
from collections import namedtuple

class Overall:
    """Overall scenario settings such as scenario title and goal,
    and app settings."""
    title = "Generic Adventure"
    description = (
        "An empty scenario. To begin, go to \"Change Setup\"."
        " You'll probably want to change the"
        " title and this description in addition to doing some basic"
        " setup such as adding a few starting items.")
    winning_item = None
    winning_quantity = 0
    game_data = None

    @classmethod
    def to_json(cls):
        return {
            'title': cls.title,
            'description': cls.description,
            'winning_item': cls.winning_item.id if cls.winning_item else None,
            'winning_quantity': cls.winning_quantity
        }

    @classmethod
    def from_json(cls, data):
        cls.title = data['title']
        cls.description = data['description']
        winning_item_id = data['winning_item']
        if winning_item_id is not None:
            cls.winning_item = Item.get_by_id(int(winning_item_id))
            cls.winning_quantity = data['winning_quantity']
        else:
            cls.winning_item = None
            cls.winning_quantity = 0

    @classmethod
    def configure_by_form(cls):
        if request.method == 'POST':
            if 'save_changes' in request.form:
                print("Saving changes.")
                print(request.form)
                cls.title = request.form.get('scenario_title')
                cls.description = request.form.get('scenario_description')
                winning_item_id = request.form.get('winning_item')
                if winning_item_id:
                    cls.winning_item = Item.get_by_id(int(winning_item_id))
                    cls.winning_quantity = int(request.form.get('winning_quantity'))
                else:
                    cls.winning_item = None
                    cls.winning_quantity = 1
            elif 'cancel_changes' in request.form:
                print("Cancelling changes.")
            else:
                print("Neither button was clicked.")
            return redirect(url_for('configure'))
        else:
            return render_template(
                'configure/overall.html', overall=cls)

CharacterRow = namedtuple('CharacterRow', ['char_id', 'char_name', 'loc_id', 'loc_name', 'action_name', 'action_link', 'user_id'])

def get_charlist_display():
    user_id = session.get('user_id')
    overall = g.game_data.overall
    # Get the user_ids from the current session game token
    game_token_user_ids = session.get('game_token_users', [])
    #game_token_user_ids = [user.user_id for user in game_token_users]
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
    # Add separate rows for each user_id from the game token that is not in
    # the character list
    for user_id in game_token_user_ids:
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
        user_id = session.get('user_id')
        overall = g.game_data.overall
        charlist = get_charlist_display()
        return render_template(
            'play/overview.html', overall=overall, current_user_id=user_id,
            charlist=charlist)

    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        return Overall.configure_by_form()

