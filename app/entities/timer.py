from flask import jsonify
import threading
from datetime import timedelta
import time
import math

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

    def calc_effective(self, amount):
        """Determine source and result quantity changes by batch.
        For example, if the speed is 6 produced per 10 seconds,
        and the exchange rate is 5 required to produce 3,
        then there will be two batches, producing 6 and requiring 10.

        In case the speed is slightly higher, it will be rounded down,
        although that may not have real-world meaning,
        so it's probably best if the speed and production qty divide evenly.
        """
        result_qty = self.item.result_qty
        batches = math.floor(amount / result_qty)  # best if no rounding needed
        effective_result_qty = batches * result_qty
        effective_quantities = {}
        for source_item, source_qty in self.item.sources.items():
            effective_source_qty = batches * source_qty
            effective_quantities[source_item] = effective_source_qty
        return effective_result_qty, effective_quantities

    def can_change_quantity(self, amount):
        effective_result_qty, effective_quantities = self.calc_effective(amount)
        for source_item, effective_source_qty in effective_quantities.items():
            if effective_source_qty > 0 and source_item.timer.quantity < effective_source_qty:
                raise Exception(
                    f"Cannot take {effective_source_qty} {source_item.name}.")

    def change_quantity(self, amount):
        effective_result_qty, effective_quantities = self.calc_effective(amount)
        try:
            self.can_change_quantity(amount)
        except Exception:
            return False
        for source_item, effective_source_qty in effective_quantities.items():
            source_item.timer.quantity -= effective_source_qty
        self.quantity += effective_result_qty
        return True

    def increment_progress(self):
        while self.is_running:
            try:
                self.can_change_quantity(self.rate_amount)
            except Exception:
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

