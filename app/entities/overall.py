from flask import (
    Flask,
    redirect,
    render_template,
    request,
    url_for
)
from .item import Item

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
            'winning_item': cls.winning_item,
            'winning_quantity': cls.winning_quantity
        }

    @classmethod
    def from_json(cls, data):
        cls.title = data['title']
        cls.description = data['description']
        winning_item_id = data['winning_item']
        if winning_item_id is not None:
            cls.winning_item = Item.get_by_id(winning_item_id)
            cls.winning_quantity = data['winning_quantity']
        else:
            cls.winning_item = None
            cls.winning_quantity = 0

    @classmethod
    def configure_by_form(cls):
        if request.method == 'POST':
            if 'save_changes' in request.form:
                print("Saving changes.")
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
def set_routes(app):
    @app.route('/overview')
    def overview():
        return render_template('play/overview.html', overall=Overall)

    @app.route('/configure/overall', methods=['GET', 'POST'])
    def configure_overall():
        return Overall.configure_by_form()

