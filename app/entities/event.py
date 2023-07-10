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
        self.base_difficulty = {  # specified on configure screen
                'Easy': 5,
                'Moderate': 10,
                'Hard': 15,
                'Very Hard': 20,
            }
        self.outcome_margin = 10
        self.adjusted_difficulty = {}  # may be adjusted on play screen
        self.stat_adjustment = 0  # for example, 5 for perception
        self.outcome = 0

    @classmethod
    def get_by_id(cls, id_to_get):
        id_to_get = int(id_to_get)
        return next(
            (instance for instance in cls.instances
            if instance.id == id_to_get), None)

    def get_outcome(self):
        difficulty = self.adjusted_difficulty or self.base_difficulty
        roll = self.stat_adjustment + roll_dice(20)
        total = roll - difficulty
        if total <= -self.outcome_margin:
            self.outcome = 0  # Critical failure
        elif total <= 0:
            self.outcome = 1  # Minor Failure
        elif total <= self.outcome_margin:
            self.outcome = 2  # Minor Success
        else:
            self.outcome = 3  # Major Success
        display = (
            "Difficulty {}, Outcome Margin {}<br>"
            "Stat Adjustment {} + 1d20 ({}) = {}<br>"
            "Outcome is a {}."
        ).format(
            difficulty,
            self.outcome_margin,
            self.stat_adjustment,
            roll,
            total,
            OUTCOMES[self.outcome],
        )
        return display

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'toplevel': self.toplevel,
            'base_difficulty': self.base_difficulty,
            'outcome_margin': self.outcome_margin,
            'stat_adjustment': self.stat_adjustment,
        }

    @classmethod
    def from_json(cls, data):
        event = cls(data['id'])
        event.name = data['name']
        event.description = data['description']
        event.toplevel = data['toplevel']
        event.base_difficulty = data['base_difficulty']
        event.outcome_margin = data['outcome_margin']
        event.stat_adjustment = data['stat_adjustment']
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
                self.base_difficulty = {
                    'Easy': 5,
                    'Moderate': 10,
                    'Hard': 15,
                    'Very Hard': 20
                }
                self.adjusted_difficulty = int(request.form.get('event_difficulty'))
                self.outcome_margin = int(request.form.get('event_outcome_margin'))
                self.stat_adjustment = int(request.form.get('event_stat_adjustment'))
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
            self.adjusted_difficulty = int(request.form.get('event_difficulty'))
            self.outcome_margin = int(request.form.get('event_outcome_margin'))
            self.stat_adjustment = int(request.form.get('event_stat_adjustment'))
            return render_template('play/event.html', current=self,
                outcome=self.get_outcome())
        else:
            return render_template('play/event.html', current=self)

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

