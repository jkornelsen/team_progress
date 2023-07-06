from flask import jsonify
import threading
from datetime import timedelta
import time

class Timer:
    def __init__(self, item, rate_amount=1.0, rate_duration=1.0, quantity=0):
        self.item = item
        self.rate_amount = rate_amount
        self.rate_duration = rate_duration
        self.quantity = quantity

        self.prev_elapsed_time = 0
        self.last_start_time = None

        self.increment_lock = threading.Lock()
        self.is_running = False

    def to_json(self):
        return {
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'quantity': self.quantity,
            'prev_elapsed_time': self.prev_elapsed_time,
        }

    @classmethod
    def from_json(cls, data, item):
        timer = cls(item)
        timer.rate_amount = data['rate_amount']
        timer.rate_duration = data['rate_duration']
        timer.quantity = data['quantity']
        timer.prev_elapsed_time = data['prev_elapsed_time']
        return timer

    def can_change_quantity(self, amount):
        for source_item, required in self.item.sources.items():
            if required > 0 and source_item.timer.quantity < required:
                print(f"Cannot subtract {required} from {source_item.name}")
                return False
        return True

    def change_quantity(self, amount):
        if not self.can_change_quantity(amount):
            return False
        for source_item, required in self.item.sources.items():
            source_item.timer.quantity -= required
        self.quantity += amount
        return True

    def increment_progress(self):
        while self.is_running:
            if not self.can_change_quantity(self.rate_amount):
                self.stop()
                break
            time.sleep(self.rate_duration)
            with self.increment_lock:
                if not self.change_quantity(self.rate_amount):
                    self.stop()
                    break

    def start(self):
        if self.rate_amount == 0:
            self.is_running = False
            return False
        if not self.is_running:
            self.is_running = True
            self.last_start_time = time.time()
            thread = threading.Thread(target=self.increment_progress)
            thread.daemon = True
            thread.start()
            return True
        else:
            return False

    def stop(self):
        if self.is_running:
            self.is_running = False
            self.prev_elapsed_time = self.calculate_elapsed_time()
            self.last_start_time = None
            return True
        else:
            return False

    def get_time(self):
        formatted_time = str(
            timedelta(seconds=int(self.calculate_elapsed_time())))
        return jsonify({'time': formatted_time})

    def calculate_elapsed_time(self):
        if self.last_start_time is not None:
            self.new_elapsed_time = time.time() - self.last_start_time
        else:
            self.new_elapsed_time = 0
        return self.prev_elapsed_time + self.new_elapsed_time

