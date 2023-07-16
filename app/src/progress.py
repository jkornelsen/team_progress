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
        self.batches_processed = 0
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
            'batches_processed': self.batches_processed,
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
        instance.batches_processed = data['batches_processed']
        instance.is_ongoing = data['is_ongoing']
        return instance

    # return true if able to change quantity
    def change_quantity(self, batches_requested):
        with self.lock:
            stop_here = False
            if batches_requested == 0:
                self.stop()
                return False
            num_batches = batches_requested
            eff_result_qty = num_batches * self.step_size
            if self.limit != 0 and abs(eff_result_qty) > abs(self.limit):
                num_batches = abs(self.limit) // abs(self.step_size)
                eff_result_qty = num_batches * self.step_size
                stop_here = True  # can't process the full amount
            eff_source_qtys = {}
            for source_item, source_qty in self.sources.items():
                eff_source_qty = num_batches * source_qty
                eff_source_qtys[source_item] = eff_source_qty
            for source_item, source_qty in self.sources.items():
                eff_source_qty = num_batches * source_qty
                if (eff_source_qty > 0
                        and source_item.progress.quantity < eff_source_qty):
                    stop_here = True  # can't process the full amount
                    num_batches = min(
                        num_batches,
                        math.floor(source_item.progress.quantity / eff_source_qty))
            if num_batches > 0:
                for source_item, source_qty in self.sources.items():
                    eff_source_qty = num_batches * source_qty
                    source_item.progress.quantity -= eff_source_qty
                eff_result_qty = num_batches * self.step_size
                self.quantity += eff_result_qty
                self.batches_processed += num_batches
            if stop_here:
                self.stop()
            return num_batches > 0

    def determine_current_quantity(self):
        elapsed_time = self.calculate_elapsed_time()
        total_batches_needed = math.floor(
            elapsed_time * (self.rate_amount * self.rate_duration))
        batches_to_do = total_batches_needed - self.batches_processed
        if batches_to_do > 0:
            return self.change_quantity(batches_to_do)
        else:
            return False

    def start(self):
        if self.rate_amount == 0 or self.is_ongoing:
            return False
        self.start_time = time.time()
        self.batches_processed = 0
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

