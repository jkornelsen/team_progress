from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for
)
import random

OUTCOMES = [
    "Critical failure",
    "Minor Failure",
    "Minor Success",
    "Major Success"]
(OUTCOME_CRITICAL_FAILURE,
 OUTCOME_MINOR_FAILURE,
 OUTCOME_MINOR_SUCCESS,
 OUTCOME_MAJOR_SUCCESS) = range(len(OUTCOMES))

def roll_dice(sides):
    return random.randint(1, sides)

class Event:
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
        self.toplevel = False if len(self.__class__.instances) > 1 else True
        self.outcome_margin = 9  # difference required to get major or critical
        self.difficulty_values = {  # specified on configure screen
                'Easy': 5,
                'Moderate': 10,
                'Hard': 15,
                'Very Hard': 20,
            }
        self.difficulty = 'Moderate'  # which one for a particular occurrence
        self.stat_adjustment = 0  # for example, 5 for perception
        self.advantage = 0  # for example +1 means best of two rolls
        self.outcome = 0

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
            'toplevel': self.toplevel,
            'difficulty_values': self.difficulty_values,
            'outcome_margin': self.outcome_margin,
        }

    @classmethod
    def from_json(cls, data):
        event = cls(data['id'])
        event.name = data['name']
        event.description = data['description']
        event.toplevel = data['toplevel']
        event.difficulty_values = data['difficulty_values']
        event.outcome_margin = data['outcome_margin']
        cls.instances.append(event)
        return event

    @classmethod
    def list_from_json(cls, json_data):
        cls.instances.clear()
        for event_data in json_data:
            cls.from_json(event_data)
        cls.last_id = max((event.id for event in cls.instances), default=0)
        return cls.instances

    def configure_by_form(self):
        if request.method == 'POST':
            if 'save_changes' in request.form:  # button was clicked
                print("Saving changes.")
                print(request.form)
                if self not in self.__class__.instances:
                    self.__class__.instances.append(self)
                self.name = request.form.get('event_name')
                self.description = request.form.get('event_description')
                self.toplevel = bool(request.form.get('top_level'))
                for difficulty in self.difficulty_values:
                    new_value = int(request.form.get(f'difficulty_{difficulty}'))
                    self.difficulty_values[difficulty] = new_value
                self.outcome_margin = int(request.form.get('event_outcome_margin'))
            elif 'delete_event' in request.form:
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
            return render_template('configure/event.html', current=self)

    def play_by_form(self):
        if request.method == 'POST':
            print("Saving changes.")
            print(request.form)
            self.difficulty = request.form.get('event_difficulty')
            self.stat_adjustment = int(request.form.get('event_stat_adjustment'))
            return render_template('play/event.html', current=self,
                outcome=self.get_outcome())
        else:
            return render_template('play/event.html', current=self)

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
        if request.method == 'GET':
            session['referrer'] = request.referrer
            print(f"Referrer in configure_event(): {request.referrer}")
        if event_id == "new":
            print("Creating a new event.")
            event = Event()
        else:
            print(f"Retrieving event with ID: {event_id}")
            event = Event.get_by_id(int(event_id))
        return event.configure_by_form()

    @app.route('/play/event/<int:event_id>', methods=['GET', 'POST'])
    def play_event(event_id):
        event = Event.get_by_id(event_id)
        if event:
            return event.play_by_form()
        else:
            return 'Event not found'

