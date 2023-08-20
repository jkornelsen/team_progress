from datetime import datetime, timedelta
from flask import jsonify
import math
import threading

from .db_serializable import Identifiable, coldef

tables_to_create = {
    'progress': f"""
        {coldef('id')},
        recipe_id integer,
        quantity integer NOT NULL,
        q_limit integer NOT NULL,
        start_time timestamp,
        stop_time timestamp,
        batches_processed integer NOT NULL,
        is_ongoing boolean NOT NULL
    """
}

class Progress(Identifiable):
    """Track progress, such as over time."""
    def __init__(self, new_id="", entity=None, recipe=None):
        super().__init__(new_id)
        self.entity = entity  # Item or other entity that uses this object
        if recipe:
            self.recipe = recipe
        else:
            from .item import Recipe
            self.recipe = Recipe()
        self.quantity = 0  # the main value tracked
        self.q_limit = 0  # limit the quantity if not 0
        self.start_time = None
        self.stop_time = None
        self.batches_processed = 0
        self.is_ongoing = False
        self.lock = threading.Lock()

    def to_json(self):
        return {
            'id': self.id,
            'recipe_id': self.recipe.id,
            'quantity': self.quantity,
            'q_limit': self.q_limit,
            'start_time': self.start_time,
            'stop_time': self.stop_time,
            'batches_processed': self.batches_processed,
            'is_ongoing': self.is_ongoing,
        }

    @classmethod
    def from_json(cls, data, entity=None):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0),), entity=entity)
        from .item import Recipe
        instance.recipe = Recipe(int(data.get('recipe_id', 0)))
        instance.quantity = data.get('quantity', 0)
        instance.q_limit = data.get('q_limit', 0)
        instance.start_time = data.get('start_time')
        instance.stop_time = data.get('stop_time')
        instance.batches_processed = data.get('batches_processed', 0)
        instance.is_ongoing = data.get('is_ongoing', False)
        return instance

    @classmethod
    def tablename(cls):
        return 'progress'  # no extra 's' at the end

    # returns true if able to change quantity
    def change_quantity(self, batches_requested):
        with self.lock:
            print(f"change_quantity() for {self.id}:"
                f" batches_requested={batches_requested}")
            stop_here = False
            if batches_requested == 0:
                raise Exception("Expected non-zero number of batches.")
            num_batches = batches_requested
            eff_result_qty = num_batches * self.recipe.rate_amount
            new_quantity = self.quantity + eff_result_qty
            if ((self.q_limit > 0.0 and new_quantity > self.q_limit)
                    or (self.q_limit < 0.0 and new_quantity < self.q_limit)):
                num_batches = (self.q_limit - self.quantity) // self.recipe.rate_amount
                stop_here = True  # can't process the full amount
            eff_source_qtys = {}
            for source_item, source_qty in self.recipe.sources.items():
                eff_source_qty = num_batches * source_qty
                eff_source_qtys[source_item] = eff_source_qty
                print(f"change_quantity() for {self.id}:"
                    f" eff_source_qty for {source_item}={eff_source_qty}")
            for source_item, source_qty in self.recipe.sources.items():
                eff_source_qty = num_batches * source_qty
                if (eff_source_qty > 0
                        and source_item.progress.quantity < eff_source_qty):
                    stop_here = True  # can't process the full amount
                    num_batches = min(
                        num_batches,
                        math.floor(source_item.progress.quantity / eff_source_qty))
            print(f"change_quantity() for {self.id}:"
                f" num_batches={num_batches}")
            if num_batches > 0:
                for source_item, source_qty in self.recipe.sources.items():
                    eff_source_qty = num_batches * source_qty
                    source_item.progress.quantity -= eff_source_qty
                    source_item.to_db()
                eff_result_qty = num_batches * self.recipe.rate_amount
                self.quantity += eff_result_qty
                self.batches_processed += num_batches
                self.entity.to_db()
            if stop_here:
                self.stop()
            return num_batches > 0

    def determine_current_quantity(self):
        elapsed_time = self.calculate_elapsed_time()
        total_batches_needed = math.floor(elapsed_time / self.recipe.rate_duration)
        batches_to_do = total_batches_needed - self.batches_processed
        print(f"determine_current_quantity: batches_to_do={batches_to_do}")
        if batches_to_do > 0:
            return self.change_quantity(batches_to_do)
        else:
            self.stop()
            return False

    def start(self, recipe_id=0):
        if recipe_id:
            for recipe in self.entity.recipes:
                if recipe.id == recipe_id:
                    self.recipe = recipe
                    break
        if self.recipe.rate_amount == 0 or self.is_ongoing:
            return False
        self.start_time = datetime.now()
        self.batches_processed = 0
        self.is_ongoing = True
        self.entity.to_db()
        return True

    def stop(self):
        if self.is_ongoing:
            self.is_ongoing = False
            self.stop_time = datetime.now()
            self.entity.to_db()
            return True
        else:
            return False

    def calculate_elapsed_time(self):
        """Returns number of seconds between start and stop time."""
        if self.is_ongoing:
            elapsed_time = datetime.now() - self.start_time
        elif self.start_time is not None and self.stop_time is not None:
            elapsed_time = self.stop_time - self.start_time
        else:
            elapsed_time = timedelta(seconds=0)
        return elapsed_time.total_seconds()

