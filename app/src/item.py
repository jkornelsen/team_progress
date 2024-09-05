from flask import g, request, session
import logging
import math

from .attrib import Attrib, AttribOf
from .db_serializable import (
    DbSerializable, Identifiable, MutableNamespace, coldef,
    DbError, DeletionError)
from .progress import Progress
from .utils import Pile, Storage, request_bool, request_float

tables_to_create = {
    'items': f"""
        {coldef('id')},
        {coldef('name')},
        {coldef('description')},
        toplevel boolean NOT NULL,
        masked boolean NOT NULL,
        storage_type varchar(20) not null,
        q_limit float(4) NOT NULL,
        quantity float(4) NOT NULL,
        progress_id integer,
        FOREIGN KEY (game_token, progress_id)
            REFERENCES progress (game_token, id)
    """,
}
logger = logging.getLogger(__name__)

class Source:
    def __init__(self, new_id=0):
        self.item = Item(new_id)  # source item, not result item
        self.pile = self.item
        self.preserve = False  # if true then source will not be consumed
        self.q_required = 1.0

    def to_json(self):
        return {
            'source_id': self.item.id,
            'preserve': self.preserve,
            'q_required': self.q_required}

    @classmethod
    def from_json(cls, data):
        instance = cls()
        instance.item = Item(int(data.get('source_id', 0)))
        instance.preserve = data.get('preserve', False)
        instance.q_required = data.get('q_required', 1.0)
        return instance

class AttribReq:
    def __init__(self, new_id=0, new_val=0):
        self.attrib = Attrib(new_id)
        self.val = new_val
        self.entity = None  # entity that fulfills the requirement

class Recipe(DbSerializable):
    def __init__(self, new_id=0, item=None):
        self.id = int(new_id)  # only unique for a particular item
        self.item_produced = item
        self.rate_amount = 1.0  # quantity produced per batch
        self.rate_duration = 3.0  # seconds for a batch
        self.instant = False
        self.sources = []  # Source objects
        self.attribs = {}  # AttribReq objects keyed by attr id

    def to_json(self):
        return {
            'recipe_id': self.id,
            'item_id': self.item_produced.id if self.item_produced else 0,
            'rate_amount': self.rate_amount,
            'rate_duration': self.rate_duration,
            'instant': self.instant,
            'sources': [source.to_json() for source in self.sources],
            'attribs': {attrib_id: req.val
                for attrib_id, req in self.attribs.items()}
            }

    @classmethod
    def from_json(cls, data, item_produced=None):
        instance = cls()
        instance.id = data.get('recipe_id', 0)
        instance.item_produced = (
            item_produced if item_produced
            else Item(int(data.get('item_id', 0))))
        instance.rate_amount = data.get('rate_amount', 1.0)
        instance.rate_duration = data.get('rate_duration', 3.0)
        instance.instant = data.get('instant', False)
        instance.sources = [
            Source.from_json(src_data)
            for src_data in data.get('sources', [])]
        instance.attribs = {
            attrib_id: AttribReq(attrib_id, val)
            for attrib_id, val in data.get('attribs', {}).items()}
        return instance

    def json_to_db(self, doc):
        logger.debug("json_to_db() for id=%d", self.id)
        self.insert_single(
            "recipes",
            "game_token, item_id, recipe_id,"
            " rate_amount, rate_duration, instant", (
                g.game_token, doc['item_id'], self.id,
                doc['rate_amount'],
                doc['rate_duration'],
                doc['instant']
                ))
        if doc['sources']:
            logger.debug("sources: %s", doc['sources'])
            values = []
            for source in doc['sources']:
                values.append((
                    g.game_token, doc['item_id'], self.id,
                    source['source_id'],
                    source['q_required'],
                    source['preserve']
                    ))
            self.insert_multiple(
                "recipe_sources",
                "game_token, item_id, recipe_id,"
                " source_id, q_required, preserve",
                values)
        if doc['attribs']:
            logger.debug("attribs: %s", doc['attribs'])
            values = []
            for attrib_id, attrib_val in doc['attribs'].items():
                values.append((
                    g.game_token, doc['item_id'], self.id,
                    attrib_id, attrib_val
                    ))
            self.insert_multiple(
                "recipe_attribs",
                "game_token, item_id, recipe_id,"
                " attrib_id, value",
                values)

class Item(Identifiable, Pile):
    PILE_TYPE = Storage.UNIVERSAL  # constant for this class
    def __init__(self, new_id=""):
        Identifiable.__init__(self, new_id)
        Pile.__init__(self, item=self)
        self.name = ""
        self.description = ""
        # Varies for different items. Typically,
        # Item.quantity will be 0 unless storage_type is universal,
        # but general storage can still be used for other types if needed.
        self.storage_type = Storage.UNIVERSAL
        self.toplevel = True
        self.masked = False
        self.attribs = {}  # AttribOf objects keyed by attrib id
        self.recipes = []  # list of Recipe objects
        self.q_limit = 0.0  # limit the quantity if not 0
        self.pile = self
        self.progress = Progress(container=self)  # for general storage

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'storage_type': self.storage_type,
            'toplevel': self.toplevel,
            'masked': self.masked,
            'recipes': [
                recipe.to_json()
                for recipe in self.recipes],
            'attribs': {
                attrib_id: attrib_of.val
                for attrib_id, attrib_of in self.attribs.items()},
            'q_limit': self.q_limit,
            'quantity': self.quantity,
            'progress': self.progress.to_json(),
        }

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.storage_type = data.get('storage_type', Storage.UNIVERSAL)
        instance.toplevel = data.get('toplevel', False)
        instance.masked = data.get('masked', False)
        instance.attribs = {
            attrib_id: AttribOf(attrib_id=attrib_id, val=val)
            for attrib_id, val in data.get('attribs', {}).items()}
        instance.q_limit = data.get('q_limit', 0.0)
        instance.quantity = data.get('quantity', 0.0)
        instance.progress = Progress.from_json(
            data.get('progress', {}), instance)
        instance.recipes = [
            Recipe.from_json(recipe_data, instance)
            for recipe_data in data.get('recipes', [])]
        return instance

    def json_to_db(self, doc):
        logger.debug("json_to_db() for id=%d", self.id)
        self.progress.json_to_db(doc['progress'])
        doc['progress_id'] = self.progress.id
        super().json_to_db(doc)
        # Delete from recipe_sources for recipes of this item_id
        self.execute_change(f"""
            DELETE FROM recipe_sources
            USING recipe_sources AS rs
            LEFT OUTER JOIN recipes ON
                recipes.game_token = rs.game_token
                AND recipes.item_id = rs.item_id
                AND recipes.recipe_id = rs.recipe_id
            WHERE recipe_sources.game_token = rs.game_token
                AND recipe_sources.item_id = rs.item_id
                AND recipe_sources.recipe_id = rs.recipe_id
                AND recipe_sources.source_id = rs.source_id
                AND recipes.item_id = %s
                AND recipes.game_token = %s
        """, [self.id, self.game_token])
        # Delete from recipe_attribs for recipes of this item_id
        self.execute_change(f"""
            DELETE FROM recipe_attribs
            USING recipe_attribs AS ra
            LEFT OUTER JOIN recipes ON
                recipes.game_token = ra.game_token
                AND recipes.item_id = ra.item_id
                AND recipes.recipe_id = ra.recipe_id
            WHERE recipe_attribs.game_token = ra.game_token
                AND recipe_attribs.item_id = ra.item_id
                AND recipe_attribs.recipe_id = ra.recipe_id
                AND recipe_attribs.attrib_id = ra.attrib_id
                AND recipes.item_id = %s
                AND recipes.game_token = %s
        """, [self.id, self.game_token])
        for rel_table in ('item_attribs', 'recipes'):
            self.execute_change(f"""
                DELETE FROM {rel_table}
                WHERE item_id = %s AND game_token = %s
            """, (self.id, self.game_token))
        if doc['attribs']:
            values = [
                (g.game_token, self.id, attrib_id, val)
                for attrib_id, val in doc['attribs'].items()]
            self.insert_multiple(
                "item_attribs",
                "game_token, item_id, attrib_id, value",
                values)
        for recipe_data in doc.get('recipes', []):
            Recipe.from_json(recipe_data, self).to_db()

    @classmethod
    def db_item_and_progress_data(cls, item_id_for_progress=None):
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
        """
        values = []
        if item_id_for_progress:
            query += "AND {tables[0]}.id = %s\n"
            values.append(item_id_for_progress);
        values.append(g.game_token);
        query += """WHERE {tables[0]}.game_token = %s
            ORDER BY {tables[0]}.name
            """
        return cls.select_tables(
            query, values, ['items', 'progress'])

    @classmethod
    def db_attrib_data(cls, id_to_get=None, include_all=False):
        if id_to_get == 0:
            return []
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.attrib_id = {tables[0]}.id
        """
        values = [g.game_token]
        if id_to_get:
            query += "AND {tables[1]}.item_id = %s\n"
            values = [id_to_get] + values
        query += "WHERE {tables[0]}.game_token = %s\n"
        if include_all:
            query += "ORDER BY {tables[0]}.name\n"
        else:
            query += "AND {tables[1]}.item_id IS NOT NULL\n"
        return cls.select_tables(
            query, values, ['attribs', 'item_attribs'])

    @classmethod
    def db_recipe_data(cls, id_to_get=None, get_by_source=False):
        if id_to_get == 0:
            return {}
        query = """
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.item_id = {tables[0]}.item_id
                AND {tables[1]}.recipe_id = {tables[0]}.recipe_id
        """
        item_conditions = [
            "WHERE {tables[0]}.game_token = %s"]
        values = [g.game_token]
        if id_to_get:
            if get_by_source:
                item_conditions.insert(0, "AND {tables[1]}.source_id = %s")
                values.insert(0, id_to_get);
            else:
                item_conditions.append("AND {tables[0]}.item_id = %s")
                values.append(id_to_get);
        query += "\n".join(item_conditions)
        sources_data = cls.select_tables(
            query, values, ['recipes', 'recipe_sources'])
        item_recipes_data = {}
        for row_recipe, row_recipe_source in sources_data:
            recipes_data = item_recipes_data.setdefault(row_recipe.item_id, {})
            recipe_data = recipes_data.setdefault(
                row_recipe.recipe_id, row_recipe)
            if row_recipe_source.source_id:
                recipe_data.setdefault('sources', []).append(row_recipe_source)
        attribs_data = []
        if get_by_source:
            return item_recipes_data
        query = """
            SELECT *
            FROM {tables[0]}
            INNER JOIN {tables[1]}
                ON {tables[1]}.game_token = {tables[0]}.game_token
                AND {tables[1]}.item_id = {tables[0]}.item_id
                AND {tables[1]}.recipe_id = {tables[0]}.recipe_id
        """
        item_conditions = [
            "WHERE {tables[0]}.game_token = %s"]
        values = [g.game_token]
        if id_to_get is not None:
            item_conditions.append("AND {tables[0]}.item_id = %s")
            values.append(id_to_get);
        query += "\n".join(item_conditions)
        attribs_data = cls.select_tables(
            query, values, ['recipes', 'recipe_attribs'])
        for row_recipe, row_recipe_attrib in attribs_data:
            recipes_data = item_recipes_data.setdefault(
                row_recipe.item_id, {})
            recipe_data = recipes_data.setdefault(
                row_recipe.recipe_id, row_recipe)
            if row_recipe_attrib.attrib_id:
                recipe_data.setdefault('attribs', {}
                    )[row_recipe_attrib.attrib_id] = row_recipe_attrib.value
        return item_recipes_data

    @classmethod
    def data_for_file(cls):
        logger.debug("data_for_file()")
        # Get item and progress data
        tables_rows = cls.db_item_and_progress_data()
        instances = {}  # keyed by ID
        for item_data, progress_data in tables_rows:
            instance = instances.setdefault(
                item_data.id, cls.from_json(vars(item_data)))
            if progress_data.id:
                instance.progress = Progress.from_json(progress_data, instance)
        # Get attrib data for items
        tables_rows = cls.db_attrib_data()
        for attrib_data, item_attrib_data in tables_rows:
            instance = instances[item_attrib_data.item_id]
            attrib_of = AttribOf.from_json(item_attrib_data)
            instance.attribs[attrib_data.id] = attrib_of
        # Get source data for items
        item_recipes_data = cls.db_recipe_data()
        for item_id, recipes_data in item_recipes_data.items():
            instance = instances[item_id]
            instance.recipes = [
                Recipe.from_json(recipe_data, instance)
                for recipe_data in recipes_data.values()]
            # Get the recipe that is currently in progress
            recipe_id = instance.progress.recipe.id
            if recipe_id:
                instance.progress.recipe = next(
                    (recipe for recipe in instance.recipes
                    if recipe.id == recipe_id), Recipe(item=instance))
        # Print debugging info
        logger.debug("found %d items", len(instances))
        for instance in instances.values():
            logger.debug("item %d (%s) has %d recipes", 
                instance.id, instance.name, len(instance.recipes))
            if len(instance.recipes):
                recipe = instance.recipes[0]
                logger.debug("recipe id %d", recipe.id)
                logger.debug("    rate_amount=%d, rate_duration=%d, instant=%s",
                    recipe.rate_amount, recipe.rate_duration, recipe.instant)
                for source in recipe.sources:
                    logger.debug("    source item id %d, qty %d",
                        source.item.id, source.q_required)
        return list(instances.values())

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        if id_to_get == 'new':
            id_to_get = 0
        else:
            id_to_get = int(id_to_get)
        # Get all item data and the current item's progress data
        tables_rows = cls.db_item_and_progress_data(id_to_get)
        g.game_data.items = []
        current_data = MutableNamespace()
        for item_data, progress_data in tables_rows:
            if item_data.id == id_to_get:
                current_data = item_data
            if progress_data.id:
                item_data.progress = progress_data
            g.game_data.items.append(Item.from_json(item_data))
        # Get all attrib data and the current item's attrib relation data
        tables_rows = cls.db_attrib_data(id_to_get, include_all=True)
        for attrib_data, item_attrib_data in tables_rows:
            if item_attrib_data.attrib_id:
                current_data.setdefault(
                    'attribs', {})[attrib_data.id] = item_attrib_data.value
            g.game_data.attribs.append(Attrib.from_json(attrib_data))
        # Get the current item's source relation data
        item_recipes_data = cls.db_recipe_data(id_to_get)
        if item_recipes_data:
            recipes_data = list(item_recipes_data.values())[0]
            current_data.recipes = list(recipes_data.values())
        # Create item from data
        current_obj = Item.from_json(current_data)
        # Replace partial objects with fully populated objects
        populated_objs = {}
        for partial_attrib, val in current_obj.attribs.items():
            attrib = Attrib.get_by_id(partial_attrib.id)
            populated_objs[attrib.id] = AttribOf(attrib, val=val)
        current_obj.attribs = populated_objs
        for recipe in current_obj.recipes:
            for source in recipe.sources:
                source.item = Item.get_by_id(source.item.id)
            for attrib_id, req in recipe.attribs.items():
                req.attrib = Attrib.get_by_id(attrib_id)
        # Print debugging info
        logger.debug(f"found %d recipes", len(current_obj.recipes))
        #if len(current_obj.recipes):
        #    recipe = current_obj.recipes[0]
        for recipe in current_obj.recipes:
            logger.debug("recipe %d rate_amount=%d instant=%s", 
                recipe.id, recipe.rate_amount, recipe.instant)
            for source in recipe.sources:
                logger.debug("source item.id %d, name %s, req %d, storage %s",
                    source.item.id, source.item.name, source.q_required,
                    source.item.storage_type)
        return current_obj

    @classmethod
    def data_for_play(cls, id_to_get, owner_char_id=0, at_loc_id=0,
            default_pile=False):
        """
        :param default_pile: False to use the type based on passed params
            such as current char inventory.
            True to use pile type that gets produced or used as a source.
        """
        logger.debug("data_for_play(%s, %s, %s)",
            id_to_get, owner_char_id, at_loc_id)
        current_obj = cls.data_for_configure(id_to_get)
        # Get all needed character and location names
        from .game_data import GameData
        from .location import Location
        GameData.entity_names_from_db([Location])
        Location.load_characters_at_loc(at_loc_id)
        # Get item data for the specific container,
        # and get piles at this loc or char that can be used for sources
        _load_piles(current_obj, owner_char_id, at_loc_id, default_pile)
        # Get relation data for items that use this item as a source
        item_recipes_data = cls.db_recipe_data(id_to_get, get_by_source=True)
        for item_id, recipes_data in item_recipes_data.items():
            item = Item.get_by_id(item_id)
            item.recipes = [
                Recipe.from_json(recipe_data, item)
                for recipe_id, recipe_data in recipes_data.items()]
        return current_obj

    def configure_by_form(self):
        if 'save_changes' in request.form:  # button was clicked
            logger.debug("Saving changes.")
            logger.debug(request.form)
            self.name = request.form.get('item_name')
            self.description = request.form.get('item_description')
            self.storage_type = request.form.get('storage_type')
            self.toplevel = request_bool(request, 'top_level')
            self.masked = request_bool(request, 'masked')
            self.q_limit = request_float(request, 'item_limit')
            self.quantity = request_float(request, 'item_quantity')
            #if self.progress.is_ongoing:
            #    self.progress.stop()
            recipe_ids = request.form.getlist('recipe_id')
            self.recipes = []
            for recipe_id in recipe_ids:
                recipe = Recipe(int(recipe_id), self)
                self.recipes.append(recipe)
                recipe.rate_amount = request_float(request,
                    f'recipe{recipe_id}_rate_amount')
                recipe.rate_duration = request_float(request,
                    f'recipe{recipe_id}_rate_duration')
                recipe.instant = request_bool(request,
                    f'recipe{recipe_id}_instant')
                source_ids = request.form.getlist(
                    f'recipe{recipe_id}_source_id')
                logger.debug(f"Source IDs: %s", source_ids)
                for source_id in source_ids:
                    source = Source.from_json({
                        'source_id': int(source_id),
                        'q_required': request_float(request,
                            f'recipe{recipe_id}_source{source_id}_qtyreq',
                            0.0),
                        'preserve': request_bool(request,
                            f'recipe{recipe_id}_source{source_id}_preserve'),
                    })
                    recipe.sources.append(source)
                    logger.debug("Sources for %s: %s",
                        recipe_id, {source.item.id: source.q_required
                        for source in recipe.sources})
                recipe_attrib_ids = request.form.getlist(
                    f'recipe{recipe_id}_attrib_id')
                for attrib_id in recipe_attrib_ids:
                    attrib_value = request_float(request,
                        f'recipe{recipe_id}_attrib{attrib_id}_value', 1.0)
                    recipe.attribs[attrib_id] = AttribReq(
                        attrib_id=attrib_id, val=attrib_value)
            attrib_ids = request.form.getlist('attrib_id')
            logger.debug("Attrib IDs: %s", attrib_ids)
            self.attribs = {}
            for attrib_id in attrib_ids:
                attrib_val = request_float(request,
                    f'attrib{attrib_id}_val', 0.0)
                self.attribs[attrib_id] = AttribOf(
                    attrib_id=attrib_id, val=attrib_val)
            logger.debug("attribs: %s", {attrib_id: attrib_of.val
                for attrib_id, attrib_of in self.attribs.items()})
            self.to_db()
        elif 'delete_item' in request.form:
            try:
                self.remove_from_db()
                session['file_message'] = 'Removed item.'
            except DbError as e:
                raise DeletionError(e)
        elif 'cancel_changes' in request.form:
            logger.debug("Cancelling changes.")
        else:
            logger.debug("Neither button was clicked.")

def _load_piles(current_item, char_id, loc_id, default_pile):
    """Assign a pile from this location or char inventory
    for the current item and that can be used for each recipe source.
    Also find chars or items that meet recipe attrib requirements.
    """
    logger.debug("load_piles()")
    from .character import Character
    from .location import Location
    chars = []
    loc = Location()
    if char_id and not loc_id:
        # Get current loc of char
        chars = Character.load_piles(char_id)
        char = next(iter(chars), Character(char_id))
        loc_id = char.location.id if char.location else 0
    if loc_id:
        # Get items for all chars at this loc
        chars = Character.load_piles(loc_id=loc_id)
        # Get all items at this loc
        loc = Location.load_piles(loc_id)
    # Assign the most appropriate pile
    current_item.pile = _assign_pile(
        current_item, chars, loc, char_id, loc_id, default_pile)
    for recipe in current_item.recipes:
        for source in recipe.sources:
            source.pile = _assign_pile(source.item, chars, loc)
        # Look for entities to meet attrib requirements
        for attrib_id, req in recipe.attribs.items():
            for item in g.game_data.items:
                attrib_of = item.attribs.get(attrib_id)
                if attrib_of is not None and attrib_of.val >= req.val:
                    req.entity = item
            for char in chars:
                attrib_of = char.attribs.get(attrib_id)
                if attrib_of is not None and attrib_of.val >= req.val:
                    req.entity = char

def _assign_pile(current_item, chars, loc, char_id=0, loc_id=0,
        default_pile=True):
    logger.debug("item id %d type %s",
        current_item.id, current_item.storage_type)
    pile = None
    if default_pile:
        pile_type = current_item.storage_type
    elif current_item.storage_type == Storage.CARRIED and char_id:
        pile_type = Storage.CARRIED
    elif current_item.storage_type == Storage.LOCAL and loc_id:
        pile_type = Storage.LOCAL
    elif char_id:
        pile_type = Storage.CARRIED
    elif loc_id:
        pile_type = Storage.LOCAL
    else:
        pile_type = Storage.UNIVERSAL
    logger.debug("pile_type %s", pile_type)
    if pile_type == Storage.CARRIED:
        # Select a char at this loc who owns one
        for char in chars:
            for owned_item_id, owned_item in char.items.items():
                if (owned_item_id == current_item.id and
                        (owned_item.quantity != 0 or not pile)):
                    pile = owned_item
                    pile.container = char
                    logger.debug("assigned ownedItem from %s qty %.1f", 
                        owned_item.container.name, pile.quantity)
        if char_id and not pile:
            from .character import Character, OwnedItem
            char = next(
                (ch for ch in chars if ch.id == char_id),
                Character.data_for_configure(char_id))
            pile = OwnedItem(current_item)
            pile.container = char
            logger.debug("assigned empty ownedItem from %s", 
                pile.container.name)
    elif pile_type == Storage.LOCAL:
        # Select an itemAt for this loc
        for item_at in loc.items:
            if (item_at.item.id == current_item.id and
                    (item_at.quantity != 0 or not pile)):
                pile = item_at
                pile.container = loc
                logger.debug("assigned itemAt from %s qty %.1f", 
                    item_at.container.name, pile.quantity)
        if loc_id and not pile:
            from .location import ItemAt, Location
            if loc.id != loc_id:
                loc = Location.data_for_configure(loc_id)
            pile = ItemAt(current_item)
            pile.container = loc
            logger.debug("assigned empty itemAt from %s", 
                pile.container.name)
    if not pile:
        pile = current_item
        pile.container = current_item
        logger.debug(f"assigned general storage qty %.1f",
            pile.quantity)
    pile.container.pile = pile
    return pile
