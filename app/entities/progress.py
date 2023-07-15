from datetime import timedelta
from flask import jsonify
import math
import threading
import time

class Progress:
    """Track progress, such as over time."""
    def __init__(self, step_size=1.0, rate_amount=1.0, rate_duration=1.0,
            quantity=0, sources=None):
        self.quantity = quantity  # the main value tracked
        self.limit = 0  # limit the quantity if not 0
        self.step_size = step_size
        self.rate_amount = rate_amount
        self.rate_duration = rate_duration
        if sources:
            self.sources = sources
        else:
            self.sources = {}
        self.start_time = None
        self.stop_time = None
        self.is_ongoing = False
        self.lock = threading.Lock()

    def to_json(self):
        return {
            'quantity': self.quantity,
            'limit': self.limit,
            'step_size': self.step_size,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'start_time': self.start_time,
            'stop_time': self.stop_time,
            'is_ongoing': self.is_ongoing,
        }

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.quantity = data['quantity']
        instance.limit = data.get('limit', 0)
        instance.step_size = data.get('step_size', 0)
        instance.rate_amount = data['rate_amount']
        instance.rate_duration = data['rate_duration']
        instance.start_time = data['start_time']
        instance.stop_time = data['stop_time']
        instance.is_ongoing = data['is_ongoing']
        return instance

    def calc_effective(self, num_batches):
        """Determine source and result quantity changes by batch.
        For example, if the speed is 6 produced per 10 seconds,
        and the exchange rate is 5 required to produce 3,
        then there will be two batches, producing 6 and requiring 10.

        In case the speed is slightly higher, it will be rounded down,
        although that may not have real-world meaning,
        so it's probably best if the speed and production qty divide evenly.
        """
        result_qty = self.step_size
        eff_result_qty = num_batches * result_qty
        eff_source_qtys = {}
        for source_item, source_qty in self.sources.items():
            eff_source_qty = num_batches * source_qty
            eff_source_qtys[source_item] = eff_source_qty
        return eff_result_qty, eff_source_qtys

    def can_change_quantity(self, num_batches):
        eff_result_qty, eff_source_qtys = self.calc_effective(num_batches)
        for source_item, eff_source_qty in eff_source_qtys.items():
            if (eff_source_qty > 0
                    and source_item.progress.quantity < eff_source_qty):
                raise Exception(
                    f"Cannot take {eff_source_qty} {source_item.name}.")

    def change_quantity(self, num_batches):
        with self.lock:
            eff_result_qty, eff_source_qtys = self.calc_effective(num_batches)
            try:
                self.can_change_quantity(num_batches)
            except Exception:
                return False
            for source_item, eff_source_qty in eff_source_qtys.items():
                source_item.progress.quantity -= eff_source_qty
            self.quantity += eff_result_qty
            if self.limit != 0 and abs(self.quantity) >= abs(self.limit):
                self.quantity = self.limit
                self.stop()
                #return False
            return True

    def determine_current_quantity(self):
        elapsed_time = self.calculate_elapsed_time()
        num_batches = math.floor(
            elapsed_time * (self.rate_amount * self.rate_duration))
        if num_batches > 0:
            return self.change_quantity(num_batches)
        else:
            return False

    def start(self):
        if self.rate_amount == 0 or self.is_ongoing:
            return False
        self.start_time = time.time()
        self.is_ongoing = True
        return True

    def stop(self):
        if self.is_ongoing:
            self.is_ongoing = False
            self.stop_time = time.time()
            return True
        else:
            return False

    def calculate_elapsed_time(self):
        if self.is_ongoing:
            elapsed_time = time.time() - self.start_time
        elif self.start_time is not None and self.stop_time is not None:
            elapsed_time = self.stop_time - self.start_time
        else:
            elapsed_time = 0
        return elapsed_time

