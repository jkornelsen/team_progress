import logging

from flask import g, session

from .attrib import Attrib, AttribFor
from .db_serializable import (
    DbError, DeletionError, Identifiable, QueryHelper, coldef)
from .progress import Progress
from .recipe import Byproduct, Recipe, Source
from .utils import RequestHelper, Storage

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
            DEFERRABLE INITIALLY DEFERRED
        """,
    }
logger = logging.getLogger(__name__)

class Pile:
    PILE_TYPE = None  # specify in child classes
    def __init__(self, item=None, container=None):
        self.item = item
        self.container = container  # character or location where item is
        self.quantity = 0

class Item(Identifiable, Pile):
    PILE_TYPE = Storage.UNIVERSAL  # Constant for this class
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
        self.attribs = {}  # AttribFor objects keyed by attrib id
        self.recipes = []  # list of Recipe objects
        self.q_limit = 0.0  # limit the quantity if not 0
        self.pile = self
        self.progress = Progress(container=self)  # for general storage

    def _base_export_data(self):
        """Prepare the base dictionary for JSON and DB."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'storage_type': self.storage_type,
            'toplevel': self.toplevel,
            'masked': self.masked,
            'q_limit': self.q_limit,
            'quantity': self.quantity,
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
        if not isinstance(data, dict):
            data = vars(data)
        instance = cls(int(data.get('id', 0)))
        instance.name = data.get('name', "")
        instance.description = data.get('description', "")
        instance.storage_type = data.get('storage_type', Storage.UNIVERSAL)
        instance.toplevel = data.get('toplevel', False)
        instance.masked = data.get('masked', False)
        instance.attribs = {
            attrib_id: AttribFor(attrib_id, val)
            for attrib_id, val in data.get('attribs', [])}
        instance.q_limit = data.get('q_limit', 0.0)
        instance.quantity = data.get('quantity', 0.0)
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
                """, (self.id, self.game_token))
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
    def load_complete_objects(cls, id_to_get=None):
        """Load objects with everything needed for storing to db
        or JSON file.
        :param id_to_get: specify to only load a single object
        """
        logger.debug("load_complete_objects(%s)", id_to_get)
        if id_to_get in ['new', '0', 0]:
            return cls()
        if id_to_get and id_to_get in g.active.items:
            logger.debug("already loaded")
            return g.active.items[id_to_get]
        # Get item and progress data
        qhelper = QueryHelper("""
            SELECT *
            FROM {tables[0]}
            LEFT JOIN {tables[1]}
                ON {tables[1]}.id = {tables[0]}.progress_id
                AND {tables[1]}.game_token = {tables[0]}.game_token
            WHERE {tables[0]}.game_token = %s
            """, [g.game_token])
        qhelper.add_limit("{tables[0]}.id", id_to_get)
        qhelper.sort_by("{tables[0]}.name")
        tables_rows = cls.select_tables(
            qhelper=qhelper, tables=['items', 'progress'])
        items = {}  # data (not objects) keyed by ID
        for item_data, progress_data in tables_rows:
            item = items.setdefault(item_data.id, item_data)
            item.progress = progress_data
        # Get attrib relation data
        qhelper = QueryHelper("""
            SELECT *
            FROM item_attribs
            WHERE game_token = %s
            """, [g.game_token])
        qhelper.add_limit("item_id", id_to_get)
        attrib_rows = cls.execute_select(qhelper=qhelper)
        for row in attrib_rows:
            item = items[row.item_id]
            item.setdefault(
                'attribs', []).append((row.attrib_id, row.value))
        # Get source relation data
        all_recipes_data = Recipe.load_complete_data(id_to_get)
        for item_id, recipes_data in all_recipes_data.items():
            item = items[item_id]
            item.recipes = recipes_data.values()
        # Set list of objects
        instances = []
        for data in items.values():
            instances.append(cls.from_data(data))
        if id_to_get:
            if not instances:
                raise ValueError(f"Could not load item {id_to_get}.")
            instance = instances[0]
            g.active.items[id_to_get] = instance
            return instance
        g.game_data.set_list(cls, instances)
        return instances

    @classmethod
    def data_for_configure(cls, id_to_get):
        logger.debug("data_for_configure(%s)", id_to_get)
        current_obj = cls.load_complete_objects(id_to_get)
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
                        related.item = cls.load_complete_objects(
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
        _load_piles(current_obj, owner_char_id, at_loc_id, main_pile_type)
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
        return current_obj

    def configure_by_form(self):
        req = RequestHelper('form')
        if req.has_key('save_changes') or req.has_key('make_duplicate'):
            req.debug()
            self.name = req.get_str('item_name')
            self.description = req.get_str('item_description')
            self.storage_type = req.get_str('storage_type')
            req = RequestHelper('form')
            self.toplevel = req.get_bool('top_level')
            self.masked = req.get_bool('masked')
            old = Item.load_complete_objects(self.id)
            self.q_limit = req.set_num_if_changed(
                req.get_str('item_limit'), old.q_limit)
            self.quantity = req.set_num_if_changed(
                req.get_str('item_quantity'), old.quantity)
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

def _load_piles(current_item, char_id, loc_id, main_pile_type):
    """Assign a pile from this location or char inventory
    for the current item and that can be used for each recipe source.
    Also find chars or items that meet recipe attrib requirements.
    """
    logger.debug(
        "_load_piles(%d, %s, %s)",
        current_item.id, char_id, loc_id)
    from .character import Character
    from .location import Location
    chars = []
    loc = Location()
    position = None
    if char_id:
        char = Character.get_by_id(char_id)
        if char:
            chars = [char]
            char_loc_id = char.location.id if char.location else 0
            if (char_loc_id != loc_id and
                    current_item.storage_type == Storage.CARRIED):
                # Use character's location instead of passed loc_id
                if char_loc_id:
                    loc_id = char_loc_id
                else:
                    loc_id = 0
        #position = char.position
    if loc_id:
        # Get all items at this loc
        #loc = Location.load_complete_objects(loc_id, position)
        loc = Location.load_complete_objects(loc_id)
        if not position:
            # TODO: assign position to first useful local pile found
            #_assign_pile(
            #    current_item, chars=[], loc, loc_id=loc_id)
            pass
    # Assign the most appropriate pile
    logger.debug("main pile")
    current_item.pile = _assign_pile(
        current_item, chars, loc, char_id, loc_id, main_pile_type)
    current_item.pile.item = current_item
    container = current_item.pile.container
    if loc_id:
        # Get items for all chars at this loc
        # TODO: if position or grid then only consider chars by that pos
        #chars = Character.load_complete_objects(
        #    loc_id=loc_id, pos=position)
        chars = [
            char for char in g.game_data.characters
            if char.location and char.location.id == loc_id]
    # This container item id was set by container.progress.from_data(),
    # loaded from the progress table.
    if container.pile.item.id != current_item.pile.item.id:
        # Don't carry over progress for a different item.
        # Replace the reference with an empty Progress object instead.
        container.progress = Progress(container=container)
    container.pile = current_item.pile
    for recipe in current_item.recipes:
        for source in recipe.sources:
            logger.debug("source pile")
            source.pile = _assign_pile(
                source.item, chars, loc, char_id, loc_id)
        # Look for entities to meet attrib requirements
        for attrib_id, req in recipe.attribs.items():
            for item in g.game_data.items:
                attrib_for = item.attribs.get(attrib_id)
                if attrib_for is not None and attrib_for.val >= req.val:
                    req.entity = item
                    logger.debug("attrib %s req %.1f met by item %s %.1f",
                        attrib_for.attrib.name, req.val,
                        item.name, attrib_for.val)
            for char in chars:
                attrib_for = char.attribs.get(attrib_id)
                if attrib_for is not None and attrib_for.val >= req.val:
                    req.entity = char
                    logger.debug("attrib %s req %.1f met by char %s %.1f",
                        attrib_for.attrib.name, req.val,
                        char.name, attrib_for.val)

def _assign_pile(
        pile_item, chars, loc, char_id=0, loc_id=0, forced_pile_type=''):
    logger.debug("_assign_pile(item.id=%d, item.type=%s, "
        "chars=[%d], loc.id=%d, char_id=%s, loc_id=%s, type=%s)",
        pile_item.id, pile_item.storage_type, len(chars),
        loc.id if loc else "_", char_id, loc_id, forced_pile_type)
    pile = None
    if forced_pile_type:
        pile_type = forced_pile_type
    elif pile_item.storage_type == Storage.CARRIED and char_id:
        pile_type = Storage.CARRIED
    elif pile_item.storage_type == Storage.LOCAL and loc_id:
        pile_type = Storage.LOCAL
    elif pile_item.storage_type == Storage.UNIVERSAL:
        pile_type = Storage.UNIVERSAL
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
                if (owned_item_id == pile_item.id and
                        (owned_item.quantity != 0 or not pile)):
                    pile = owned_item
                    pile.container = char
                    logger.debug("assigned ownedItem from %s qty %.1f",
                        owned_item.container.name, pile.quantity)
        if char_id and not pile:
            from .character import Character, OwnedItem
            char = Character.get_by_id(char_id)
            pile = OwnedItem(pile_item, char)
            char.items[pile_item.id] = pile
            logger.debug(
                "assigned empty ownedItem from %s", pile.container.name)
    elif pile_type == Storage.LOCAL:
        # Select an itemAt for this loc
        for item_at in loc.items.values():
            if (item_at.item.id == pile_item.id and
                    (item_at.quantity != 0 or not pile)):
                pile = item_at
                pile.container = loc
                logger.debug(
                    "assigned itemAt from %s qty %.1f",
                    item_at.container.name, pile.quantity)
        if loc_id and not pile:
            from .location import ItemAt, Location
            if loc.id != loc_id:
                loc = Location.data_for_configure(loc_id)
            pile = ItemAt(pile_item)
            pile.container = loc
            loc.items[pile_item.id] = pile
            logger.debug(
                "assigned empty itemAt from %s", pile.container.name)
    if not pile:
        pile = pile_item
        pile.container = pile_item
        logger.debug(
            "assigned general storage qty %.1f", pile.quantity)
    return pile
