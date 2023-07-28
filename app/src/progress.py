from datetime import timedelta
from flask import jsonify
import math
import threading
import time

from .db_serializable import Identifiable, coldef

tables_to_create = {
    'progress': f"""
        {coldef('id')},
        quantity FLOAT(2) NOT NULL,
        q_limit FLOAT(2) NOT NULL,
        step_size FLOAT(2) NOT NULL,
        rate_amount FLOAT(2) NOT NULL,
        rate_duration FLOAT(2) NOT NULL,
        sources_json TEXT NOT NULL,
        start_time TIMESTAMP,
        stop_time TIMESTAMP,
        batches_processed INTEGER NOT NULL,
        is_ongoing BOOLEAN NOT NULL
    """
}

class Progress(Identifiable):
    """Track progress, such as over time.
    Instead of its own table, the data for this class will be stored in
    the database for the entity that contains it.
    """
    def __init__(self, entity, step_size=1.0,
            rate_amount=1.0, rate_duration=1.0, quantity=0.0, sources=None):
        self.entity = entity  # Item or other entity that uses this object
        self.quantity = quantity  # the main value tracked
        self.q_limit = 0.0  # limit the quantity if not 0
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
            'q_limit': self.q_limit,
            'step_size': self.step_size,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'start_time': self.start_time,
            'stop_time': self.stop_time,
            'batches_processed': self.batches_processed,
            'is_ongoing': self.is_ongoing,
        }

    @classmethod
    def from_json(cls, data, entity):
        instance = cls(entity)
        instance.quantity = data['quantity']
        instance.q_limit = data.get('q_limit', 0.0)
        instance.step_size = data.get('step_size', 0)
        instance.rate_amount = data['rate_amount']
        instance.rate_duration = data['rate_duration']
        instance.start_time = data['start_time']
        instance.stop_time = data['stop_time']
        instance.batches_processed = data['batches_processed']
        instance.is_ongoing = data['is_ongoing']
        return instance

    # returns true if able to change quantity
    def change_quantity(self, batches_requested):
        with self.lock:
            print(f"Changing quantity: batches_requested={batches_requested}")
            stop_here = False
            if batches_requested == 0:
                raise Exception("Expected non-zero number of batches.")
            num_batches = batches_requested
            eff_result_qty = num_batches * self.step_size
            new_quantity = self.quantity + eff_result_qty
            if ((self.q_limit > 0.0 and new_quantity > self.q_limit)
                    or (self.q_limit < 0.0 and new_quantity < self.q_limit)):
                num_batches = (self.q_limit - self.quantity) // self.step_size
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
                    source_item.to_db()
                eff_result_qty = num_batches * self.step_size
                self.quantity += eff_result_qty
                self.batches_processed += num_batches
                self.entity.to_db()
            if stop_here:
                self.stop()
            return num_batches > 0

    def determine_current_quantity(self):
        elapsed_time = self.calculate_elapsed_time()
        total_batches_needed = math.floor(
            elapsed_time * (self.rate_amount * self.rate_duration))
        batches_to_do = total_batches_needed - self.batches_processed
        print(f"determine_current_quantity: batches_to_do={batches_to_do}")
        if batches_to_do > 0:
            return self.change_quantity(batches_to_do)
        else:
            self.stop()
            return False

    def start(self):
        if self.rate_amount == 0 or self.is_ongoing:
            return False
        self.start_time = time.time()
        self.batches_processed = 0
        self.is_ongoing = True
        self.entity.to_db()
        return True

    def stop(self):
        if self.is_ongoing:
            self.is_ongoing = False
            self.stop_time = time.time()
            self.entity.to_db()
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

