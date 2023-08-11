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
import random
from types import SimpleNamespace

from .db_serializable import Identifiable, coldef, new_game_data
from .attrib import Attrib

OUTCOME_TYPES = [
    'fourway',  # critical/minor failure or success
    'numeric',  # such as a damage number
    'selection']  # random selection from a list
(OUTCOME_FOURWAY,
 OUTCOME_NUMERIC,
 OUTCOME_SELECTION) = OUTCOME_TYPES
OUTCOMES = [
    "Critical Failure",
    "Minor Failure",
    "Minor Success",
    "Major Success"]
(OUTCOME_CRITICAL_FAILURE,
 OUTCOME_MINOR_FAILURE,
 OUTCOME_MINOR_SUCCESS,
 OUTCOME_MAJOR_SUCCESS) = range(len(OUTCOMES))
BASE_DIFFICULTY = [
    SimpleNamespace(name='Easy', val=5),
    SimpleNamespace(name='Moderate', val=10),
    SimpleNamespace(name='Hard', val=15),
    SimpleNamespace(name='Very Hard', val=20)]
(DIFFICULTY_EASY,
 DIFFICULTY_MODERATE,
 DIFFICULTY_HARD,
 DIFFICULTY_VERY_HARD) = BASE_DIFFICULTY
OUTCOME_MARGIN = 9  # difference required to get major or critical

def roll_dice(sides):
    return random.randint(1, sides)

tables_to_create = {
    'events': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        {coldef('toplevel')},
        outcome_type varchar(20) not null,
        trigger_chance integer[],
        numeric_range integer[],
    """
}

class Event(Identifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.toplevel = False if len(self.get_list()) > 1 else True
        self.outcome_type = OUTCOME_FOURWAY
        self.trigger_chance = (0, 1) # tuple (numerator, denominator)
        self.numeric_range = (0, 10)  # tuple (min, max)
        self.determining_attrs = []  # Attrib objects determining the outcome
        self.changed_attrs = []  # Attrib objects changed by the outcome
        ## For a particular occurrence, not stored in Event table
        self.difficulty = 'Moderate'  # which one for a particular occurrence
        self.stat_adjustment = 0  # for example, 5 for perception
        self.advantage = 0  # for example +1 means best of two rolls
        self.outcome = 0

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'outcome_type': self.outcome_type,
        }

    @classmethod
    def from_json(cls, data, _=None):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.toplevel = data.get('toplevel', False)
        instance.outcome_type = data.get('outcome_type', OUTCOME_FOURWAY)
        return instance

    @classmethod
    def data_for_configure(cls, config_id):
        print(f"{cls.__name__}.data_for_configure()")
        if config_id == 'new':
            config_id = 0
        else:
            config_id = int(config_id)
        # Get all attrib names
        attribs_data = cls.execute_select("""
            SELECT id, name
            FROM attribs
            WHERE game_token = %s
            ORDER BY name
        """, (g.game_token,))
        g.game_data.attribs = [
            Attrib.from_json(attrib_data)
            for attrib_data in attribs_data]
        return cls.from_db(config_id)

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            print("Saving changes.")
            print(request.form)
            entity_list = self.get_list()
            if self not in entity_list:
                entity_list.append(self)
            self.name = request.form.get('event_name')
            self.description = request.form.get('event_description')
            self.toplevel = bool(request.form.get('top_level'))
            for difficulty in self.difficulty_values:
                new_value = int(request.form.get(f'difficulty_{difficulty}'))
                self.difficulty_values[difficulty] = new_value
            self.outcome_margin = int(request.form.get('event_outcome_margin'))
            self.to_db()
        elif 'delete_event' in request.form:
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

    def play_by_form(self):
        if request.method == 'POST':
            print("Saving changes.")
            print(request.form)
            self.difficulty = request.form.get('event_difficulty')
            self.stat_adjustment = int(request.form.get('event_stat_adjustment'))
            self.to_db()
            return render_template(
                'play/event.html',
                current=self,
                outcome=self.get_outcome())
        else:
            return render_template(
                'play/event.html',
                current=self)

    def get_outcome(self):
        difficulty_value = self.difficulty_values[self.difficulty]
        roll = roll_dice(20)
        total = roll + self.stat_adjustment - difficulty_value
        if total <= -self.outcome_margin:
            self.outcome = OUTCOME_CRITICAL_FAILURE
        elif total <= 0:
            self.outcome = OUTCOME_MINOR_FAILURE
        elif total < self.outcome_margin:
            self.outcome = OUTCOME_MINOR_SUCCESS
        else:
            self.outcome = OUTCOME_MAJOR_SUCCESS
        display = (
            "1d20 ({}) + Stat Adjustment {} - Difficulty {} = {}<br>"
            "Outcome is a {}."
        ).format(
            roll,
            self.stat_adjustment,
            difficulty_value,
            total,
            OUTCOMES[self.outcome],
        )
        return display


def set_routes(app):
    @app.route('/configure/event/<event_id>', methods=['GET', 'POST'])
    def configure_event(event_id):
        new_game_data()
        instance = Event.data_for_configure(event_id)
        if request.method == 'GET':
            session['referrer'] = request.referrer
            return render_template(
                'configure/event.html',
                current=instance,
                game_data=g.game_data)
        else:
            return instance.configure_by_form()

    @app.route('/play/event/<int:event_id>', methods=['GET', 'POST'])
    def play_event(event_id):
        event = Event.get_by_id(event_id)
        if event:
            return event.play_by_form()
        else:
            return 'Event not found'

