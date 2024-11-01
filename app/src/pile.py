import logging

from flask import g

from .db_serializable import DbSerializable
from .progress import Progress
from .utils import Storage

logger = logging.getLogger(__name__)

class Pile(DbSerializable):
    def __init__(self, item=None, container=None, item_id=0):
        super().__init__()
        self.item = item
        self.item_id = item_id
        self.container = container  # character or location where item is
        self.quantity = 0

    @staticmethod
    def container_type():
        """Short string to refer to the container class."""
        raise NotImplementedError()

    def set_basic_data(self, data):
        """Set the item ID so that from_data() in the child class can
        instantiate it.
        That way we don't need to import Item here and potentially cause
        circular references.
        """
        data = self.prepare_dict(data)
        self.item_id = int(data.get('item_id', 0))
        self.quantity = data.get('quantity', 0)

def load_piles(current_item, char_id, loc_id, main_pile_type):
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
        #loc = Location.load_complete_object(loc_id, position)
        loc = Location.load_complete_object(loc_id)
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
    item_piles_at_loc = []
    from .item import Item
    for item in g.game_data.items:
        if item.pile.quantity != 0:  # general storage
            pile = item.pile
            pile.item = Item.get_by_id(item.id)
            item_piles_at_loc.append(pile)
    if loc_id:
        # Get items for all chars at this loc
        # TODO: if position or grid then only consider chars by that pos
        #chars = Character.load_complete_objects(
        #    loc_id=loc_id, pos=position)
        chars = [
            char for char in g.game_data.characters
            if char.location and char.location.id == loc_id]
        for item_at in loc.items.values():
            if item_at.quantity != 0:
                item_at.item = Item.get_by_id(item_at.item.id)
                item_piles_at_loc.append(item_at)
        for char in chars:
            for owned_item_id, owned_item in char.items.items():
                if owned_item.quantity != 0:
                    owned_item.item = Item.get_by_id(owned_item.item.id)
                    item_piles_at_loc.append(owned_item)
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
        for byproduct in recipe.byproducts:
            logger.debug("byproduct pile")
            byproduct.pile = _assign_pile(
                byproduct.item, chars, loc, char_id, loc_id, main_pile_type)
        # Look for entities to meet attrib requirements
        for attrib_id, req in recipe.attribs.items():
            logger.debug("attrib %s req %.1f", attrib_id, req.val);
            for pile in item_piles_at_loc:
                item = pile.item
                attrib_for = item.attribs.get(attrib_id)
                if attrib_for:
                    test_eq = bool(req.attrib.enum)
                    logger.debug(
                        "item %s container %s val %.1f test_eq %s",
                        item.name, pile.container.name, attrib_for.val,
                        test_eq)
                    if (test_eq and attrib_for.val == req.val) or (
                            not test_eq and attrib_for.val >= req.val):
                        req.entity = item
                        logger.debug("attrib %s req %.1f met by item %s %.1f",
                            attrib_for.attrib_id, req.val,
                            item.name, attrib_for.val)
                else:
                    logger.debug(
                        "item %s container %s only has attribs %s",
                        item.id, pile.container.name, item.attribs.keys())
            for char in chars:
                attrib_for = char.attribs.get(attrib_id)
                if attrib_for and attrib_for.val >= req.val:
                    req.entity = char
                    logger.debug("attrib %s req %.1f met by char %s %.1f",
                        attrib_for.attrib_id, req.val,
                        char.name, attrib_for.val)

def _assign_pile(
        pile_item, chars, loc, char_id=0, loc_id=0, forced_pile_type=''):
    logger.debug("_assign_pile(item.id=%d, item.type=%s, "
        "chars=[%d], loc.id=%d, char_id=%s, loc_id=%s, type=%s)",
        pile_item.id, pile_item.storage_type, len(chars),
        loc.id if loc else "_", char_id, loc_id, forced_pile_type)
    from .character import Character, OwnedItem
    from .item import GeneralPile
    from .location import ItemAt, Location
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
                # XXX: If there are two candidates but only one
                # has enough to meet source.q_required,
                # mightn't this choose the one that falls short?
                if (owned_item_id == pile_item.id and
                        (owned_item.quantity != 0 or not pile)):
                    pile = owned_item
                    pile.container = char
                    logger.debug("assigned ownedItem from %s qty %.1f",
                        owned_item.container.name, pile.quantity)
        if char_id and not pile:
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
            if loc.id != loc_id:
                loc = Location.data_for_configure(loc_id)
            pile = ItemAt(pile_item, loc)
            loc.items[pile_item.id] = pile
            logger.debug(
                "assigned empty itemAt from %s", pile.container.name)
    if not pile:
        pile = GeneralPile(pile_item)
        pile_item.pile = pile
        logger.debug(
            "assigned general storage qty %.1f", pile.quantity)
    return pile
