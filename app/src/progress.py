from datetime import datetime, timedelta
import logging
import math
import threading

from flask import g

from .db_serializable import Identifiable, QueryHelper, coldef
from .utils import format_num

logger = logging.getLogger(__name__)
tables_to_create = {
    'progress': f"""
        {coldef('id')},
        item_id integer,
        recipe_id integer,
        start_time timestamp,
        stop_time timestamp,
        batches_processed integer NOT NULL,
        is_ongoing boolean NOT NULL
        """
    }

class Progress(Identifiable):
    """Track progress over time."""
    def __init__(self, new_id="", container=None, recipe=None):
        super().__init__(new_id)
        self.container = container  # e.g. Character that uses this object
        if recipe:
            self.recipe = recipe
        else:
            from .item import Recipe
            self.recipe = Recipe()  # use default values
        self.start_time = None
        self.stop_time = None
        self.batches_processed = 0
        self.is_ongoing = False
        self.lock = threading.Lock()
        self.failure_reason = ""  # error message for caller to read

    @property
    def pile(self):
        if self.container:
            return self.container.pile
        return None

    @property
    def q_limit(self):
        if self.pile:
            return self.pile.item.q_limit
        return 0.0

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'item_id': self.pile.item.id
                if self.pile and self.pile.item else 0,
            'recipe_id': self.recipe.id,
            'start_time': self.start_time,
            'stop_time': self.stop_time,
            'batches_processed': self.batches_processed,
            'is_ongoing': self.is_ongoing,
            }

    def dict_for_json(self):
        if ((not self.start_time) or
                (not self.is_ongoing and self.batches_processed == 0)):
            return {}
        return self._base_export_data()

    @classmethod
    def from_data(cls, data, container=None):
        data = cls.prepare_dict(data)
        instance = cls(data.get('id') or 0, container=container)
        from .item import Item, Recipe
        if container:
            instance.pile.item = Item(data.get('item_id') or 0)
        instance.recipe = Recipe(data.get('recipe_id') or 0)
        instance.start_time = data.get('start_time')
        instance.stop_time = data.get('stop_time')
        instance.batches_processed = data.get('batches_processed') or 0
        instance.is_ongoing = data.get('is_ongoing') or False
        return instance

    @classmethod
    def tablename(cls):
        return 'progress'  # no extra 's' at the end

    @classmethod
    def load_base_data(cls, entity_cls, id_to_get=None):
        """Load all (or the specified id) from the entity's base table
        along with progress data.
        """
        logger.debug("load_complete_objects(%s)", id_to_get)
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit("{tables[0]}.id", id_to_get)
        tables_rows = entity_cls.select_tables(
            qhelper=qhelper, tables=[entity_cls.tablename(), 'progress'])
        entities_data = {}  # data (not objects) keyed by ID
        for entity_rows, progress_rows in tables_rows:
            entity_data = entities_data.setdefault(entity_rows.id, entity_rows)
            entity_data.progress = progress_rows
        return entities_data

    # returns true if able to change quantity
    def change_quantity(self, batches_requested):
        with self.lock:
            logger.debug("change_quantity() for progress %d: batches_requested=%d",
                self.id, batches_requested)
            stop_when_done = False
            if batches_requested == 0:
                raise ValueError("Expected non-zero number of batches.")
            num_batches = batches_requested
            if not self.can_produce():
                num_batches = 0
                stop_when_done = True
            eff_result_qty = num_batches * self.recipe.rate_amount
            new_quantity = self.pile.quantity + eff_result_qty
            if self.pile.item.exceeds_limit(new_quantity):
                num_batches = ((self.q_limit - self.pile.quantity)
                    // self.recipe.rate_amount)
                stop_when_done = True  # can't process the full amount
                logger.debug(
                    "change_quantity(): num_batches=%d due to limit %d",
                    num_batches, self.q_limit)
                if num_batches == 0:
                    self.report_failure("Limit {self.q_limit} reached.")
            for source in self.recipe.sources:
                eff_source_qty = num_batches * source.q_required
                if eff_source_qty > 0:
                    logger.debug(
                        "change_quantity(): source %d, source.q_required=%d, "
                        "eff_source_qty=%.1f, source.pile.quantity=%.1f",
                        source.item.id, source.q_required, eff_source_qty,
                        source.pile.quantity)
                    if (source.pile.quantity < eff_source_qty
                            and not source.preserve):
                        stop_when_done = True  # can't process the full amount
                        num_batches = min(
                            num_batches,
                            math.floor(source.pile.quantity / source.q_required))
                        if num_batches == 0:
                            self.report_failure(
                                f"Requires {format_num(f'{source.q_required}')} "
                                f"{source.item.name}.")
                    elif source.pile.quantity < source.q_required:
                        stop_when_done = True
                        num_batches = 0
                        self.report_failure(
                            f"Requires {format_num(f'{source.q_required}')} "
                            f"{source.item.name}.")
            logger.debug("change_quantity(): num_batches=%d", num_batches)
            if num_batches > 0:
                # Deduct source quantity used
                for source in self.recipe.sources:
                    if not source.preserve:
                        eff_source_qty = num_batches * source.q_required
                        logger.debug(
                            "change_quantity(): %s -= %s for id %s",
                            source.pile.quantity, eff_source_qty,
                            source.pile.item.id)
                        source.pile.quantity -= eff_source_qty
                        logger.debug(
                            "change_quantity(): source.pile.container[%s].to_db()",
                            source.pile.container.name)
                        source.pile.container.to_db()
                # Add quantity produced
                eff_result_qty = num_batches * self.recipe.rate_amount
                logger.debug(
                    "change_quantity(): %s += %s for id %s",
                    self.pile.quantity, eff_result_qty, self.pile.item.id)
                self.pile.quantity += eff_result_qty
                self.batches_processed += num_batches
                logger.debug(
                    "change_quantity(): self.container[%s].to_db()",
                    self.container.name)
                self.container.to_db()
                # Add byproducts produced
                for byproduct in self.recipe.byproducts:
                    eff_byproduct_qty = num_batches * byproduct.rate_amount
                    logger.debug(
                        "change_quantity(): %s += %s for id %s",
                        byproduct.pile.quantity, eff_byproduct_qty,
                        byproduct.pile.item.id)
                    byproduct.pile.quantity += eff_byproduct_qty
                    logger.debug(
                        "change_quantity(): byproduct.pile.container[%s].to_db()",
                        byproduct.pile.container.name)
                    byproduct.pile.container.to_db()
            if not self.can_produce():
                stop_when_done = True
            if stop_when_done:
                self.stop()
            return num_batches > 0

    def batches_for_elapsed_time(self):
        """Returns number of seconds spent if any work gets done."""
        self.set_recipe_by_id()
        elapsed_time = self.calculate_elapsed_time()
        total_batches_needed = math.floor(elapsed_time / self.recipe.rate_duration)
        batches_to_do = total_batches_needed - self.batches_processed
        logger.debug(
            "batches_for_elapsed_time(): batches_to_do=%d (%.1f / %.1f - %d)",
            batches_to_do, elapsed_time, self.recipe.rate_duration,
            self.batches_processed)
        if batches_to_do > 0:
            success = self.change_quantity(batches_to_do)
            time_spent = batches_to_do * self.recipe.rate_duration
            if success:
                return time_spent
        return 0

    def set_recipe_by_id(self, recipe_id=0):
        if not recipe_id:
            recipe_id = self.recipe.id
            if not recipe_id:
                return
        for recipe in self.pile.item.recipes:
            if recipe.id == recipe_id:
                self.recipe = recipe
                return

    def can_produce(self, recipe_id=None):
        """True if at least one batch can be produced."""
        if recipe_id is not None:
            self.set_recipe_by_id(recipe_id)
        if self.recipe is None:
            self.report_failure("No recipe.")
            return False
        if not self.recipe.rate_amount:
            self.report_failure("Recipe production rate is 0.")
            return False
        if ((self.q_limit > 0.0 and self.pile.quantity >= self.q_limit)
                or (self.q_limit < 0.0 and self.pile.quantity <= self.q_limit)):
            self.report_failure("Limit {self.q_limit} reached.")
            return False
        for source in self.recipe.sources:
            req_qty = source.q_required
            if (req_qty > 0 and source.pile.quantity < req_qty):
                self.report_failure(
                    f"Requires {format_num(f'{req_qty}')} {source.item.name}.")
                return False
        for req in self.recipe.attribs.values():
            if (req.val > 0 and req.entity is None):
                self.report_failure(
                    f"Requires attribute {req.attrib.name} {req.val:.1f}")
                return False
        logger.debug("can produce")
        return True

    def start(self, recipe_id=0):
        self.set_recipe_by_id(recipe_id)
        if self.is_ongoing:
            logger.debug("Already ongoing.")
            return True
        if self.recipe.rate_amount == 0:
            self.report_failure("Recipe production rate is 0.")
            return False
        if not self.can_produce():
            self.stop()
            return False
        self.start_time = datetime.now()
        self.batches_processed = 0
        self.is_ongoing = True
        self.container.to_db()
        return True

    def stop(self):
        if self.is_ongoing:
            self.is_ongoing = False
            self.stop_time = datetime.now()
            self.container.to_db()
            return True
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

    def report_failure(self, message):
        """Store error message for caller to read."""
        self.failure_reason = message
        logger.info(message)
