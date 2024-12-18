import logging

from flask import g
from psycopg2.extras import NumericRange

from .attrib import AttribFor
from .db_serializable import (
    DependentIdentifiable, QueryHelper, Serializable, coldef)
from .utils import format_num

logger = logging.getLogger(__name__)
tables_to_create = {
    'recipes': f"""
        {coldef('id')},
        item_id integer NOT NULL,
        rate_amount float(4) NOT NULL,
        rate_duration float(4) NOT NULL,
        instant boolean NOT NULL,
        FOREIGN KEY (game_token, item_id)
            REFERENCES items (game_token, id)
            ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED
        """,
    }

class Source(Serializable):
    def __init__(self, new_id=0):
        self.item_id = new_id  # source item, not produced item
        self.item = None
        self.pile = None
        self.preserve = False  # if true then source will not be consumed
        self.q_required = 1.0

    def _base_export_data(self):
        return {
            'item_id': self.item_id,
            'preserve': self.preserve,
            'q_required': self.q_required,
            }

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = cls(data.get('item_id', 0))
        instance.preserve = data.get('preserve', False)
        instance.q_required = data.get('q_required', 1.0)
        return instance

class Byproduct(Serializable):
    def __init__(self, new_id=0):
        self.item_id = new_id  # item produced
        self.item = None
        self.pile = None
        self.rate_amount = 1.0

    def _base_export_data(self):
        return {
            'item_id': self.item_id,
            'rate_amount': self.rate_amount,
            }

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = cls(data.get('item_id', 0))
        instance.rate_amount = data.get('rate_amount', 1.0)
        return instance

class AttribReq(AttribFor):
    """Required value of an attribute."""
    def __init__(self, attrib_id=0, value_range=None, show_max=True):
        super().__init__(attrib_id)
        if isinstance(value_range, NumericRange):
            low = float(value_range.lower)
            high = float(value_range.upper)
            if low == float('-inf'):
                low = None
            if high == float('inf'):
                high = None
        else:
            low, high = (
                value_range if value_range is not None
                else [1, None])
        if low is not None and high is not None and low > high:
            low, high = high, low
        self.value_range = [low, high]
        self.show_max = show_max  # true to show range on form to be within

    def _base_export_data(self):
        return {
            'attrib_id': self.attrib_id,
            'value_range': self.value_range,
            'show_max': self.show_max,
            }

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = cls(
            data.get('attrib_id', 0),
            data.get('value_range', None)
            )
        instance.show_max = data.get('show_max', False)
        return instance

    def in_range(self, val):
        low, high = self.value_range
        if low is not None and val < low:
            return False
        if high is not None and val > high:
            return False
        return True

    def bounded(self):
        low, high = self.value_range
        return not (low is None and high is None)

    def min_str(self):
        low, _ = self.value_range
        return low if low is not None else ''

    def max_str(self):
        _, high = self.value_range
        return high if high is not None else ''

    def pg_range_str(self):
        low, high = self.value_range
        if low is None:
            low = '-infinity'
        if high is None:
            high = '+infinity'
        return f'[{low},{high}]'

    def range_str(self):
        """Display in error messages and logs."""
        low, high = self.value_range
        def format_value(val):
            #return f"{val:.1f}" if val is not None else None
            return format_num(val) if val is not None else None
        if not self.bounded():
            return "any"
        if low is not None and high is None:
            return f"≥ {format_value(low)}"
        if low is None and high is not None:
            return f"≤ {format_value(high)}"
        if low is not None and high is not None:
            if low == high:
                return f"{format_value(low)}"
            return f"{format_value(low)} - {format_value(high)}"
        return "unknown"

    def enum_range_str(self):
        """Display range for enum values, for example, 'Mon - Fri'."""
        if not self.attrib or not self.attrib.enum:
            return self.range_str()
        low, high = self.value_range
        if low is not None:
            low = int(low)
        if high is not None:
            high = int(high)
        enum = self.attrib.enum
        if low is not None and high is not None:
            if low == high:
                return enum[low]
            else:
                return f"{enum[low]} - {enum[high]}"
        if low is not None:
            return f"≥ {enum[low]}"
        if high is not None:
            return f"≤ {enum[high]}"
        return "unknown"

class Recipe(DependentIdentifiable):
    def __init__(self, new_id=0, item=None):
        super().__init__(new_id)
        self.item_produced = item
        self.rate_amount = 1.0  # quantity produced per batch
        self.rate_duration = 3.0  # seconds for a batch
        self.instant = False
        self.sources = []  # Source objects
        self.byproducts = []  # Byproduct objects
        self.attrib_reqs = {}  # AttribReq objects keyed by attr id

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'instant': self.instant,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'sources': [source.dict_for_json() for source in self.sources],
            'byproducts': [byp.dict_for_json() for byp in self.byproducts],
            'attrib_reqs': [
                req.dict_for_json() for req in self.attrib_reqs.values()],
            })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'item_id': self.item_produced.id if self.item_produced else 0
            })
        return data

    @classmethod
    def from_data(cls, data, item_produced=None):
        data = cls.prepare_dict(data)
        instance = cls()
        instance.id = data.get('id', 0)
        from .item import Item
        instance.item_produced = item_produced
        instance.rate_amount = data.get('rate_amount', 1.0)
        instance.rate_duration = data.get('rate_duration', 3.0)
        instance.instant = data.get('instant', False)
        instance.sources = [
            Source.from_data(src_data)
            for src_data in data.get('sources', [])]
        instance.byproducts = [
            Byproduct.from_data(byp_data)
            for byp_data in data.get('byproducts', [])]
        instance.attrib_reqs = {
            req_data.get('attrib_id'): AttribReq.from_data(req_data)
            for req_data in data.get('attrib_reqs', [])}
        return instance

    def to_db(self):
        logger.debug("to_db() for id=%d", self.id)
        super().to_db()
        if self.sources:
            logger.debug("sources: %s", self.sources)
            values = []
            for source in self.sources:
                values.append((
                    g.game_token, self.id,
                    source.item_id,
                    source.q_required,
                    source.preserve,
                    ))
            self.insert_multiple(
                "recipe_sources",
                "game_token, recipe_id, item_id, q_required, preserve",
                values)
        if self.byproducts:
            logger.debug("byproducts: %s", self.byproducts)
            values = []
            for byproduct in self.byproducts:
                values.append((
                    g.game_token, self.id,
                    byproduct.item_id,
                    byproduct.rate_amount,
                    ))
            self.insert_multiple(
                "recipe_byproducts",
                "game_token, recipe_id, item_id, rate_amount",
                values)
        if self.attrib_reqs:
            logger.debug("attrib_reqs: %s", self.attrib_reqs)
            values = []
            for attrib_id, req in self.attrib_reqs.items():
                values.append((
                    g.game_token, self.id,
                    attrib_id,
                    req.pg_range_str(),
                    req.show_max,
                    ))
            self.insert_multiple(
                "recipe_attrib_reqs",
                "game_token, recipe_id, attrib_id, value_range, show_max",
                values)

    @classmethod
    def item_relation_data(cls, id_to_get, by_source=False):
        if by_source:  # reverse
            field = "{tables[1]}.item_id"
            join_type = 'INNER'  # only the requested relation data
        else:  # forward -- as product
            field = "{tables[0]}.item_id"
            join_type = 'LEFT'  # all recipe data
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            """ + join_type + """ JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.recipe_id = {tables[0]}.id
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit(field, id_to_get)
        source_rows = cls.select_tables(
            qhelper=qhelper, tables=['recipes', 'recipe_sources'])
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.recipe_id = {tables[0]}.id
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit(field, id_to_get)
        byproduct_rows = cls.select_tables(
            qhelper=qhelper, tables=['recipes', 'recipe_byproducts'])
        return source_rows, byproduct_rows

    @classmethod
    def load_complete_data(cls, id_to_get=None):
        """Load all recipe data needed for creating Item objects
        that can be stored to db or JSON file.
        :param id_to_get: specify to only load a single recipe
        :returns: dict of recipes for each item
        """
        logger.debug("load_complete_data(%s)", id_to_get)
        if id_to_get is not None and cls.empty_values([id_to_get]):
            return {}
        # Get recipe, source, and byproduct data
        source_rows, byproduct_rows = cls.item_relation_data(id_to_get)
        item_recipes = {}  # recipe data keyed by item ID
        for recipe_row, source_row in source_rows:
            recipes_data = item_recipes.setdefault(recipe_row.item_id, {})
            recipe_data = recipes_data.setdefault(recipe_row.id, recipe_row)
            if source_row.item_id:
                recipe_data.setdefault('sources', []).append(source_row)
        for recipe_row, byproduct_row in byproduct_rows:
            if byproduct_row.recipe_id:
                recipes_data = item_recipes[recipe_row.item_id]
                recipe_data = recipes_data[recipe_row.id]
                recipe_data.setdefault('byproducts', []).append(byproduct_row)
        # Get attrib relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.recipe_id = {tables[0]}.id
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit("{tables[0]}.item_id", id_to_get)
        attribreq_rows = cls.select_tables(
            qhelper=qhelper, tables=['recipes', 'recipe_attrib_reqs'])
        for recipe_row, req_row in attribreq_rows:
            recipes_data = item_recipes[recipe_row.item_id]
            recipe_data = recipes_data[recipe_row.id]
            if req_row.attrib_id:
                recipe_data.setdefault('attrib_reqs', []).append(req_row)
        return item_recipes

    @classmethod
    def load_complete_data_dict(cls, ids):
        if not ids:
            return cls.load_complete_data(None)
        if cls.empty_values(ids):
            return {}
        merged_dict = {}
        for id_ in map(int, ids):
            data = cls.load_complete_data(id_)
            merged_dict.update(data)
        return merged_dict

    @classmethod
    def load_data_by_source(cls, id_to_get):
        """What is the specified item used for."""
        logger.debug("load_data_by_source(%s)", id_to_get)
        if cls.empty_values([id_to_get]):
            return {}
        source_rows, byproduct_rows = cls.item_relation_data(
            id_to_get, by_source=True)
        item_recipes = {}  # recipe data keyed by item ID
        for recipe_row, source_row in source_rows:
            recipes_data = item_recipes.setdefault(recipe_row.item_id, {})
            recipe_data = recipes_data.setdefault(recipe_row.id, recipe_row)
            recipe_data.setdefault('sources', []).append(source_row)
        for recipe_row, byproduct_row in byproduct_rows:
            if byproduct_row.recipe_id:
                recipes_data = item_recipes.setdefault(recipe_row.item_id, {})
                recipe_data = recipes_data.setdefault(recipe_row.id, recipe_row)
                recipe_data.setdefault('byproducts', []).append(byproduct_row)
        return item_recipes
