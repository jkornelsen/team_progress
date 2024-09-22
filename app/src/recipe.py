import logging

from flask import g

from .attrib import AttribFor
from .db_serializable import (
    Identifiable, QueryHelper, Serializable, coldef)

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
logger = logging.getLogger(__name__)

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
        instance = cls(data.get('item_id', 0))
        instance.rate_amount = data.get('rate_amount', 1.0)
        return instance

class Recipe(Identifiable):
    def __init__(self, new_id=0, item=None):
        super().__init__(new_id)
        self.item_produced = item
        self.rate_amount = 1.0  # quantity produced per batch
        self.rate_duration = 3.0  # seconds for a batch
        self.instant = False
        self.sources = []  # Source objects
        self.byproducts = []  # Byproduct objects
        self.attribs = {}  # AttribFor objects keyed by attr id

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'item_id': self.item_produced.id if self.item_produced else 0,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'instant': self.instant,
            }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'sources': [source.dict_for_json() for source in self.sources],
            'byproducts': [byp.dict_for_json() for byp in self.byproducts],
            'attribs': {attrib_id: req.val
                for attrib_id, req in self.attribs.items()}
            })
        return data

    @classmethod
    def from_data(cls, data, item_produced=None):
        instance = cls()
        instance.id = data.get('id', 0)
        from .item import Item
        instance.item_produced = (
            item_produced if item_produced
            else Item(int(data.get('item_id', 0))))
        instance.rate_amount = data.get('rate_amount', 1.0)
        instance.rate_duration = data.get('rate_duration', 3.0)
        instance.instant = data.get('instant', False)
        instance.sources = [
            Source.from_data(src_data)
            for src_data in data.get('sources', [])]
        instance.byproducts = [
            Byproduct.from_data(byp_data)
            for byp_data in data.get('byproducts', [])]
        instance.attribs = {
            attrib_id: AttribFor(attrib_id, val)
            for attrib_id, val in data.get('attribs', [])}
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
        if self.attribs:
            logger.debug("attribs: %s", self.attribs)
            values = []
            for attrib_id, req in self.attribs.items():
                values.append((
                    g.game_token, self.id,
                    attrib_id, req.val
                    ))
            self.insert_multiple(
                "recipe_attribs",
                "game_token, recipe_id, attrib_id, value",
                values)

    @classmethod
    def load_complete_data(cls, id_to_get):
        """Load all recipe data needed for creating Item objects
        that can be stored to db or JSON file.
        :param id_to_get: specify to only load a single recipe
        :returns: dict of recipes for each item
        """
        logger.debug("load_complete_data(%s)", id_to_get)
        if id_to_get in ['new', '0', 0]:
            return {}
        # Get recipe and source data
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.recipe_id = {tables[0]}.id
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit("{tables[0]}.item_id", id_to_get)
        source_rows = cls.select_tables(
            qhelper=qhelper, tables=['recipes', 'recipe_sources'])
        item_recipes = {}  # recipe data keyed by item ID
        for recipe_row, source_row in source_rows:
            recipes_data = item_recipes.setdefault(recipe_row.item_id, {})
            recipe_data = recipes_data.setdefault(recipe_row.id, recipe_row)
            if source_row.item_id:
                recipe_data.setdefault('sources', []).append(source_row)
        # Get byproduct relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.recipe_id = {tables[0]}.id
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit("{tables[0]}.item_id", id_to_get)
        byproduct_rows = cls.select_tables(
            qhelper=qhelper, tables=['recipes', 'recipe_byproducts'])
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
        attrib_rows = cls.select_tables(
            qhelper=qhelper, tables=['recipes', 'recipe_attribs'])
        for recipe_row, attrib_row in attrib_rows:
            recipes_data = item_recipes[recipe_row.item_id]
            recipe_data = recipes_data[recipe_row.id]
            if attrib_row.attrib_id:
                recipe_data.setdefault(
                    'attribs', {})[attrib_row.attrib_id] = attrib_row.value
        return item_recipes

    @classmethod
    def load_data_by_source(cls, id_to_get):
        logger.debug("load_data_by_source(%s)", id_to_get)
        if id_to_get in ['new', '0', 0, None]:
            return {}
        # Get recipe and source data
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.recipe_id = {tables[0]}.id
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit("{tables[1]}.item_id", id_to_get)
        source_rows = cls.select_tables(
            qhelper=qhelper, tables=['recipes', 'recipe_sources'])
        item_recipes = {}  # recipe data keyed by item ID
        for recipe_row, source_row in source_rows:
            recipes_data = item_recipes.setdefault(recipe_row.item_id, {})
            recipe_data = recipes_data.setdefault(recipe_row.id, recipe_row)
            if source_row.item_id:
                recipe_data.setdefault('sources', []).append(source_row)
        return item_recipes
