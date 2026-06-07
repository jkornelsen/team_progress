import logging
from flask import g
from app.models import (
    db, StorageType, GENERAL_ID, Entity, Item, Character, Location,
    Pile, ItemLimit, Progress)
from app.database import safe_remove
from .logic_discovery import check_item_unmasking

logger = logging.getLogger(__name__)

def get_or_create_pile(item_id, owner_id, position=None, slot=None):
    """Retrieves an existing inventory pile or initializes a new one.

    - If position is None, it looks for/creates an 'unplaced' pile.
    - If position is provided, it looks for/creates a pile at those coordinates.
    """
    game_token = g.game_token
    
    # Query for exact match on Token, Item, Owner, and Grid Position
    pile = Pile.query.filter_by(
        game_token=game_token,
        item_id=item_id,
        owner_id=owner_id,
        position=position
    ).first()
    
    if not pile:
        pile = Pile(
            game_token=game_token,
            item_id=item_id,
            owner_id=owner_id,
            position=position,
            slot=slot,
            quantity=0.0
        )
        db.session.add(pile)
        
    return pile

def get_accessible_quantity(item_id, owner_id):
    """
    Returns the total quantity of an item available to the owner.
    If owner is a Character, includes items at their current location.
    """
    game_token = g.game_token
    total = 0.0

    # 1. Get stock from the primary owner
    primary_piles = Pile.query.filter_by(
        game_token=game_token, item_id=item_id, owner_id=owner_id
    ).all()
    total += sum(p.quantity for p in primary_piles)

    # 2. If the owner is a Character, add stock from their current Location
    owner_entity = Entity.query.get((game_token, owner_id))
    if owner_entity and owner_entity.entity_type == 'character':
        char = Character.query.get((game_token, owner_id))
        if char and char.location_id:
            loc_piles = Pile.query.filter_by(
                game_token=game_token, 
                item_id=item_id, 
                owner_id=char.location_id
            ).all()
            total += sum(p.quantity for p in loc_piles)

    return total

def set_quantity(item_id, owner_id, new_value, position=None, slot=None):
    """
    Overwrites the quantity of an item pile.
    Useful for manual edits or event results where the final total is pre-calculated.
    """
    pile = get_or_create_pile(item_id, owner_id, position, slot)
    pile.quantity = float(new_value)
    if pile.quantity == 0:
        safe_remove(pile)
        return 0.0
        
    return pile.quantity

def get_quantity_limit(item_id, owner_id):
    """
    Returns the specific limit for an item/owner pair if it exists,
    otherwise returns the item's default q_limit.
    """
    game_token = g.game_token
    
    # Check for a specific override
    specific_limit = ItemLimit.query.filter_by(
        game_token=game_token, 
        item_id=item_id, 
        owner_id=owner_id
    ).first()
    if specific_limit:
        return specific_limit.q_limit
        
    # Fallback to Item default
    item = Item.query.get((game_token, item_id))
    return item.q_limit if item else 0.0

def adjust_quantity(item_id, owner_id, delta, position=None, slot=None):
    """
    Increases or decreases an item quantity for a specific owner.
    - delta: positive to add, negative to subtract
    - Returns: Remainder that could not be processed (overflow or unpaid debt).
    """
    game_token = g.game_token
    pile = get_or_create_pile(item_id, owner_id, position, slot)
    item = Item.query.get((g.game_token, item_id))
    remainder = 0.0

    logger.debug(
        f"adjust_quantity() Item:{item.name} | Owner:{owner_id}"
        f" | Delta:{delta} | Current:{pile.quantity}")
    
    # Case A: Adding items
    if delta > 0:
        limit = get_quantity_limit(item_id, owner_id)
        if limit > 0:
            space_left = max(0.0, limit - pile.quantity)
            if delta > space_left:
                # This much won't fit
                remainder = delta - space_left
                delta = space_left
        pile.quantity += delta

    # Case B: Removing items
    elif delta < 0:
        amount_to_remove = abs(delta)
        if pile.quantity >= 0 and amount_to_remove > pile.quantity:
            # Debt we couldn't pay
            remainder = -(amount_to_remove - pile.quantity)
            pile.quantity = 0
        else:
            pile.quantity -= amount_to_remove

    # Gained an item for the first time
    if delta > 0 and (pile.quantity - delta) <= 0:
        check_item_unmasking(game_token, item_id, was_gained=True)
    
    # Cleanup empty rows
    if abs(pile.quantity) <= 0.000000001:
        safe_remove(pile)
        
    return remainder

def transfer_item(item_id, from_owner_id, to_owner_id, quantity, 
                  from_pos=None, to_pos=None, to_slot=None):
    """Moves quantity from one Entity to another."""
    if quantity <= 0:
        return False, ''

    # 1. Check if source has enough
    source_pile = Pile.query.filter_by(
        game_token=g.game_token,
        item_id=item_id,
        owner_id=from_owner_id,
        position=from_pos
    ).first()

    # 2. Remove requested amount from source
    adjust_quantity(item_id, from_owner_id, -quantity, position=from_pos)

    # 3. Add to target and capture how much didn't fit
    overflow = adjust_quantity(
        item_id, to_owner_id, quantity, position=to_pos, slot=to_slot)

    # 4. If target full/partially full, put remainder back where it came from
    if overflow > 0:
        adjust_quantity(item_id, from_owner_id, overflow, position=from_pos)
        actual_moved = quantity - overflow
        return True, f"Inventory full: only took {actual_moved:g}."

    return True, ''

# ------------------------------------------------------------------------
# Convenience Accessors
# ------------------------------------------------------------------------

def set_item_slot(char_id, item_id, slot_name):
    """Equips an item into a specific slot."""
    pile = Pile.query.filter_by(
        game_token=g.game_token,
        item_id=item_id,
        owner_id=char_id
    ).first()
    
    if pile:
        pile.slot = slot_name
        return True
    return False
