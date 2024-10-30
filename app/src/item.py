import logging

from flask import g, session, url_for

from .attrib import Attrib, AttribFor
from .db_serializable import (
    DbError, DeletionError, CompleteIdentifiable, QueryHelper, coldef)
from .pile import Pile, load_piles
from .progress import Progress
from .recipe import Byproduct, Recipe, Source
from .utils import RequestHelper, Storage

logger = logging.getLogger(__name__)
tables_to_create = {
    'items': f"""
        {coldef('name')},
        toplevel boolean not null,
        masked boolean not null,
        mult boolean not null,
        storage_type varchar(20) not null,
        q_limit real not null,
        quantity real not null,
        progress_id integer,
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
            DEFERRABLE INITIALLY DEFERRED
        """,
    }

class GeneralPile(Pile):
    def __init__(self, item=None, quantity=None):
        super().__init__(item=item, container=item)
        if quantity is not None:
            self.quantity = quantity
        elif item and hasattr(item, 'pile') and hasattr(item.pile, 'quantity'):
            self.quantity = item.pile.quantity

    @staticmethod
    def container_type():
        return 'general'

    def dict_for_json(self):
        return self.item.dict_for_json()

    def dict_for_main_table(self):
        return self.item.dict_for_main_table()

class Item(CompleteIdentifiable):
    def __init__(self, new_id=""):
        super().__init__(new_id)
        self.name = ""
        self.description = ""
        self.storage_type = Storage.CARRIED  # usually matches pile type
        self.toplevel = False
        self.masked = False
        self.mult = False
        self.attribs = {}  # AttribFor objects keyed by attrib id
        self.recipes = []  # list of Recipe objects
        self.q_limit = 0.0  # limit the quantity if not 0
        self.progress = Progress(container=self)  # for general storage
        self.pile = GeneralPile(self)

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'storage_type': self.storage_type,
            'toplevel': self.toplevel,
            'masked': self.masked,
            'mult': self.mult,
            'q_limit': self.q_limit,
            'quantity': self.pile.quantity,
        }

    def dict_for_json(self):
        data = self._base_export_data()
        data.update({
            'recipes': [
                recipe.dict_for_json()
                for recipe in self.recipes],
            'attribs': [
                attrib_for.as_tuple()
                for attrib_for in self.attribs.values()],
            'progress': self.progress.dict_for_json(),
            })
        return data

    def dict_for_main_table(self):
        data = self._base_export_data()
        data.update({
            'progress_id': self.progress.id or None
            })
        return data

    @classmethod
    def from_data(cls, data):
        data = cls.prepare_dict(data)
        instance = super().from_data(data)
        instance.storage_type = data.get('storage_type', Storage.UNIVERSAL)
        instance.toplevel = data.get('toplevel', False)
        instance.masked = data.get('masked', False)
        instance.mult = data.get('mult', False)
        instance.attribs = {
            attrib_id: AttribFor(attrib_id, val)
            for attrib_id, val in data.get('attribs', [])}
        instance.q_limit = data.get('q_limit', 0.0)
        instance.pile = GeneralPile(
            instance, data.get('quantity', 0.0))
        instance.progress = Progress.from_data(
            data.get('progress', {}), instance)
        instance.recipes = [
            Recipe.from_data(recipe_data, instance)
            for recipe_data in data.get('recipes', [])]
        # Get the recipe that is currently in progress
        recipe_id = instance.progress.recipe.id
        if recipe_id:
            instance.progress.recipe = next(
                (recipe for recipe in instance.recipes
                if recipe.id == recipe_id), Recipe(item=instance))
        return instance

    def to_db(self):
        logger.debug("to_db() for id=%d", self.id)
        self.progress.to_db()
        super().to_db()
        for rel_table in ('item_attribs', 'recipes'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE item_id = %s AND game_token = %s
                """, (self.id, g.game_token))
        if self.attribs:
            values = [
                (g.game_token, self.id, attrib_id, attrib_for.val)
                for attrib_id, attrib_for in self.attribs.items()]
            self.insert_multiple(
                "item_attribs",
                "game_token, item_id, attrib_id, value",
                values)
        for recipe in self.recipes:
            recipe.to_db()

    @classmethod
    def load_complete_objects(cls, ids=None):
        logger.debug("load_complete_objects(%s)", ids)
        if ids:
            if cls.empty_values(ids):
                return [cls()]
            # Check if all IDs are already loaded
            loaded_items = [
                g.active.items[id_]
                for id_ in ids
                if id_ in g.active.items]
            if len(loaded_items) == len(ids):
                logger.debug("already loaded")
                return loaded_items
        items = Progress.load_base_data_dict(cls, ids)
        # Get attrib relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM item_attribs
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit_in("item_id", ids)
        attrib_rows = cls.execute_select(qhelper=qhelper)
        for row in attrib_rows:
            item = items[row.item_id]
            item.setdefault(
                'attribs', []).append((row.attrib_id, row.value))
        # Get source relation data
        all_recipes_data = Recipe.load_complete_data_dict(ids)
        for item_id, recipes_data in all_recipes_data.items():
            item = items[item_id]
            item.recipes = recipes_data.values()
        # Set list of objects
        instances = {}
        for data in items.values():
            instances[data.id] = cls.from_data(data)
        if ids and any(ids):
            if not instances:
                raise ValueError(f"Could not load items {ids}.")
            setattr(g.active, cls.listname(), instances)
        else:
            g.game_data.set_list(cls, instances.values())
        return instances.values()

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_object(id_to_get)
        # Get all basic attrib and item data
        g.game_data.from_db_flat([Attrib, Item])
        # Replace partial objects with fully populated objects
        for attrib_id, attrib_for in current_obj.attribs.items():
            attrib_for.attrib = Attrib.get_by_id(attrib_id)
        for recipe in current_obj.recipes:
            for source in recipe.sources:
                source.item = Item.get_by_id(source.item_id)
            for byproduct in recipe.byproducts:
                byproduct.item = Item.get_by_id(byproduct.item_id)
            for attrib_id, req in recipe.attribs.items():
                req.attrib = Attrib.get_by_id(attrib_id)
        return current_obj

    @classmethod
    def data_for_play(
            cls, id_to_get, owner_char_id=0, at_loc_id=0,
            complete_sources=True, main_pile_type=''):
        """
        :param complete_sources: needed to safely call source.to_db() after
            potentially modifying source quantities
        """
        logger.debug(
            "data_for_play(%s, %s, %s, %s)",
            id_to_get, owner_char_id, at_loc_id, main_pile_type)
        current_obj = cls.data_for_configure(id_to_get)
        for recipe in current_obj.recipes:
            for related_list in (recipe.sources, recipe.byproducts):
                for related in related_list:
                    if complete_sources:
                        related.item = cls.load_complete_object(
                            related.item_id)
                    else:
                        related.item = Item.get_by_id(related.item_id)
        # Get all needed location and character data
        from .location import Location
        from .character import Character
        g.game_data.entity_names_from_db([Location])
        Character.load_complete_objects()
        # Get item data for the specific container,
        # and get piles at this loc or char that can be used for sources
        load_piles(current_obj, owner_char_id, at_loc_id, main_pile_type)
        # Get relation data for items that use this item as a source
        item_recipes_data = Recipe.load_data_by_source(id_to_get)
        for item_id, recipes_data in item_recipes_data.items():
            item = Item.get_by_id(item_id)
            item.recipes = [
                Recipe.from_data(recipe_data, item)
                for recipe_id, recipe_data in recipes_data.items()]
            for recipe in item.recipes:
                for related_list in (recipe.sources, recipe.byproducts):
                    for related in related_list:
                        related.item = Item.get_by_id(related.item_id)
        from .event import Event
        Event.load_triggers_for_type(id_to_get, cls.typename())
        return current_obj

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes') or req.has_key('make_duplicate'):
            req.debug()
            self.name = req.get_str('item_name')
            self.description = req.get_str('item_description')
            self.storage_type = req.get_str('storage_type')
            session['default_storage_type'] = self.storage_type
            self.toplevel = req.get_bool('top_level')
            self.masked = req.get_bool('masked')
            self.mult = req.get_bool('mult')
            old = Item.load_complete_object(self.id)
            self.q_limit = req.set_num_if_changed(
                req.get_str('item_limit'), old.q_limit)
            self.pile = GeneralPile(
                self, req.set_num_if_changed(
                req.get_str('item_quantity'), old.pile.quantity))
            self.recipes = []
            for recipe_id, recipe_id_from in zip(
                    req.get_list('recipe_id'),
                    req.get_list('recipe_id_from')
                    ):
                if recipe_id_from == 'from_db':
                    recipe = Recipe(int(recipe_id), self)
                else:
                    recipe = Recipe(0, self)  # generate from db
                prefix = f'recipe{recipe_id}_'
                recipe.rate_amount = req.get_float(f'{prefix}rate_amount')
                recipe.rate_duration = req.get_float(f'{prefix}rate_duration')
                recipe.instant = req.get_bool(f'{prefix}instant')
                source_ids = req.get_list(f'{prefix}source_id')
                logger.debug("Source IDs: %s", source_ids)
                for source_id in source_ids:
                    source_prefix = f'{prefix}source{source_id}_'
                    source = Source.from_data({
                        'item_id': source_id,
                        'q_required': req.get_float(
                            f'{source_prefix}qtyreq', 0.0),
                        'preserve': req.get_bool(
                            f'{source_prefix}preserve'),
                        })
                    recipe.sources.append(source)
                    logger.debug("Sources for %s: %s",
                        recipe_id, {source.item_id: source.q_required
                        for source in recipe.sources})
                byproduct_ids = req.get_list(f'{prefix}byproduct_id')
                for byproduct_id in byproduct_ids:
                    byproduct_prefix = f'{prefix}byproduct{byproduct_id}_'
                    byproduct = Byproduct.from_data({
                        'item_id': byproduct_id,
                        'rate_amount': req.get_float(
                            f'{byproduct_prefix}rate_amount', 1.0),
                        })
                    recipe.byproducts.append(byproduct)
                recipe_attrib_ids = req.get_list(f'{prefix}attrib_id')
                for attrib_id in recipe_attrib_ids:
                    attrib_prefix = f'{prefix}attrib{attrib_id}_'
                    attrib_value = req.get_float(f'{attrib_prefix}value', 1.0)
                    recipe.attribs[attrib_id] = AttribFor(
                        attrib_id, attrib_value)
                self.recipes.append(recipe)
            attrib_ids = req.get_list('attrib_id[]')
            logger.debug("Attrib IDs: %s", attrib_ids)
            self.attribs = {}
            for attrib_id in attrib_ids:
                attrib_prefix = f'attrib{attrib_id}_'
                attrib_val = req.get_float(f'{attrib_prefix}val', 0.0)
                self.attribs[attrib_id] = AttribFor(attrib_id, attrib_val)
            logger.debug("attribs: %s", {attrib_id: attrib_for.val
                for attrib_id, attrib_for in self.attribs.items()})
            self.to_db()
        elif req.has_key('delete_item'):
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed item.'
                session['referrer'] = url_for('configure_index')
            except DbError as e:
                raise DeletionError(str(e))
        elif req.has_key('cancel_changes'):
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")

    def exceeds_limit(self, quantity):
        return (
            (self.q_limit > 0.0 and quantity > self.q_limit) or
            (self.q_limit < 0.0 and quantity < self.q_limit)
            )
