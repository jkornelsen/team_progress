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
        self.is_running = False
        self.increment_lock = threading.Lock()
        self.prev_elapsed_time = 0
        self.last_start_time = None

    def to_json(self):
        return {
            'quantity': self.quantity,
            'limit': self.limit,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'prev_elapsed_time': self.prev_elapsed_time,
        }

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.quantity = data['quantity']
        instance.limit = data.get('limit', 0)
        instance.rate_amount = data['rate_amount']
        instance.rate_duration = data['rate_duration']
        instance.prev_elapsed_time = data['prev_elapsed_time']
        return instance

    def calc_effective(self, amount):
        """Determine source and result quantity changes by batch.
        For example, if the speed is 6 produced per 10 seconds,
        and the exchange rate is 5 required to produce 3,
        then there will be two batches, producing 6 and requiring 10.

        In case the speed is slightly higher, it will be rounded down,
        although that may not have real-world meaning,
        so it's probably best if the speed and production qty divide evenly.
        """
        result_qty = self.step_size
        batches = math.floor(amount / result_qty)  # best if no rounding needed
        eff_result_qty = batches * result_qty
        eff_source_qtys = {}
        for source_item, source_qty in self.sources.items():
            eff_source_qty = batches * source_qty
            eff_source_qtys[source_item] = eff_source_qty
        return eff_result_qty, eff_source_qtys

    def can_change_quantity(self, amount):
        eff_result_qty, eff_source_qtys = self.calc_effective(amount)
        for source_item, eff_source_qty in eff_source_qtys.items():
            if (eff_source_qty > 0
                    and source_item.progress.quantity < eff_source_qty):
                raise Exception(
                    f"Cannot take {eff_source_qty} {source_item.name}.")

    def change_quantity(self, amount):
        eff_result_qty, eff_source_qtys = self.calc_effective(amount)
        try:
            self.can_change_quantity(amount)
        except Exception:
            return False
        for source_item, eff_source_qty in eff_source_qtys.items():
            source_item.progress.quantity -= eff_source_qty
        self.quantity += eff_result_qty
        if self.limit != 0 and abs(self.quantity) >= abs(self.limit):
            self.quantity = self.limit
            self.stop()
        return True

    def increment_progress(self):
        wait_periods = math.floor(self.rate_duration)
        wait_time = math.floor(self.rate_duration / wait_periods)
        while self.is_running:
            try:
                self.can_change_quantity(self.rate_amount)
            except Exception:
                self.stop()
                return
            for _ in range(wait_periods):
                time.sleep(wait_time)
                if not self.is_running:
                    return
            try:
                self.can_change_quantity(self.rate_amount)
            except Exception:
                self.stop()
                return
            with self.increment_lock:
                if not self.change_quantity(self.rate_amount):
                    self.stop()
                    return

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

