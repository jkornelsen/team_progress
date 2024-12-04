from datetime import datetime, timedelta
import logging
import math
import threading

from flask import g

from .db_serializable import DependentIdentifiable, QueryHelper, coldef
from .recipe import Recipe
from .user_interaction import MessageLog
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

class Progress(DependentIdentifiable):
    """Track progress over time."""
    def __init__(self, new_id="", pholder=None, pile=None, recipe=None):
        super().__init__(new_id)
        self.pholder = pholder  # Character or Item that stores Progress data
        self.pile = pile  # Item produced -- quantity gets changed
        if recipe:
            self.recipe = recipe
        else:
            self.recipe = Recipe()  # use default values
        self.start_time = None
        self.stop_time = None
        self.batches_processed = 0
        self.is_ongoing = False
        self.lock = threading.Lock()
        self.failure_reason = ""  # error message for caller to read

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
    def from_data(cls, data, pholder=None):
        data = cls.prepare_dict(data)
        instance = cls(data.get('id') or 0, pholder=pholder)
        from .item import Item
        if pholder and instance.pile and not instance.pile.item:
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
    def load_base_data_dict(cls, entity_cls, ids=None):
        """Load all (or the specified IDs) from the entity's base table
        along with progress data.
        """
        logger.debug("load_base_data_list(%s)", ids)
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit_in("{tables[0]}.id", ids)
        tables_rows = entity_cls.select_tables(
            qhelper=qhelper, tables=[entity_cls.tablename(), 'progress'])
        entities_data = {}  # data (not objects) keyed by ID
        for entity_rows, progress_rows in tables_rows:
            entity_data = entities_data.setdefault(entity_rows.id, entity_rows)
            entity_data.progress = progress_rows
        return entities_data

    def pholder_to_db(self):
        logger.debug("self.pholder[%s].to_db()", self.pholder.name)
        self.pholder.to_db()  # should include writing progress obj to db
        if self.pile and (self.pholder != self.pile.container or
                self.pholder.typename() != self.pile.container.typename() or
                self.pholder.id != self.pile.container.id):
            # Expected when pile is local item at loc, storing quantity.
            # In that case, self.pholder is Item, storing Progress data.
            logger.debug(
                "self.pile.container[%s].to_db()", self.pile.container.name)
            self.pile.container.to_db()

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
                    self.report_failure(f"Limit {self.q_limit} reached.")
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
                self.pholder_to_db()
                if self.pile.item.name:
                    message = (
                        f"{self.pile.item.name} increased"
                        f" by {format_num(eff_result_qty)}")
                    if self.pile.container.name != self.pile.item.name:
                        message = f"{self.pile.container.name}'s " + message
                    MessageLog.add(message);
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

    def batches_for_elapsed_time(self, elapsed_time=None):
        """Returns the number of batches completed, along with
        the fractional part of the next batch, if any.
        """
        self.set_recipe_by_id()
        elapsed_time = elapsed_time or self.calculate_elapsed_time()
        batch_ratio = elapsed_time / self.recipe.rate_duration
        total_batches_needed = math.floor(batch_ratio)
        fractional_batches = batch_ratio - total_batches_needed
        batches_to_do = total_batches_needed - self.batches_processed
        if batches_to_do < 1:
            batches_to_do = 0
        logger.debug(
            "batches_for_elapsed_time(): batches_to_do=%d (%.1f / %.1f - %d),"
            " remainder=%.1f",
            batches_to_do, elapsed_time, self.recipe.rate_duration,
            self.batches_processed, fractional_batches)
        if batches_to_do and self.pile:
            if not self.change_quantity(batches_to_do):
                return 0, 0
        return batches_to_do, fractional_batches

    def set_recipe_by_id(self, recipe_id=0):
        if not self.pile:
            return
        if not recipe_id:
            recipe_id = self.recipe.id
            if not recipe_id:
                return
        self.recipe = None
        for recipe in self.pile.item.recipes:
            if recipe.id == recipe_id:
                self.recipe = recipe
                return

    def can_produce(self, recipe_id=None):
        """True if at least one batch can be produced."""
        logger.debug("can_produce(%s)", recipe_id)
        if recipe_id:
            self.set_recipe_by_id(recipe_id)
        if self.recipe is None:
            self.report_failure("No recipe.")
            return False
        limit_positive = (
            self.q_limit > 0.0
            and self.pile.quantity >= self.q_limit
            and self.recipe.rate_amount > 0
            )
        limit_negative = (
            self.q_limit < 0.0
            and self.pile.quantity <= self.q_limit
            and self.recipe.rate_amount < 0
            )
        if limit_positive or limit_negative:
            self.report_failure(f"Limit {self.q_limit} reached.")
            return False
        for source in self.recipe.sources:
            req_qty = source.q_required
            if (req_qty > 0 and
                    (not source.pile or source.pile.quantity < req_qty)):
                self.report_failure(
                    f"Requires {format_num(f'{req_qty}')} {source.item.name}.")
                return False
        for req in self.recipe.attrib_reqs.values():
            if (req.bounded() and req.subject is None):
                self.report_failure(
                    f"Requires {req.attrib.name} {req.range_str()}")
                return False
        logger.debug("can produce")
        return True

    def start(self, recipe_id=0):
        logger.debug("start()")
        self.set_recipe_by_id(recipe_id)
        if self.is_ongoing:
            logger.debug("Already ongoing.")
            return True
        if not self.can_produce():
            return False
        self.start_time = datetime.now()
        self.batches_processed = 0
        self.is_ongoing = True
        self.pholder_to_db()
        return True

    def stop(self):
        logger.debug("stop()")
        if self.is_ongoing:
            self.is_ongoing = False
            self.stop_time = datetime.now()
            self.pholder_to_db()
            return True
        self.pholder_to_db()
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
