import logging
from flask import g
from app.models import (
    db, Pile, Entity, Item, Character, Location, StorageType, GENERAL_ID,
    Progress)
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

def resolve_recipe_sources(game_token, host_id, recipe, context_id=None):
    """
    Returns a list of resolved source data for a recipe.
    Respects StorageType restrictions.
    'context_id' allows looking up local/carried items relative to a 
    specific actor, even if the 'host_id' is General Storage.
    """
    resolved_sources = []
    
    # 1. Determine Context
    # Check both the host and the optional context_id for a location
    location_id = None
    effective_host_id = host_id
    
    search_ids = [host_id]
    if context_id and context_id != host_id:
        search_ids.append(context_id)

    for eid in search_ids:
        if not eid or eid == GENERAL_ID:
            continue
        ent = Entity.query.get((game_token, eid))
        if not ent: 
            continue
        
        if ent.entity_type == Character.TYPENAME:
            char = Character.query.get((game_token, eid))
            if char.location_id and not location_id:
                location_id = char.location_id
            if effective_host_id == GENERAL_ID:
                effective_host_id = char.id
        elif ent.entity_type == Location.TYPENAME:
            if not location_id: 
                location_id = ent.id
            if effective_host_id == GENERAL_ID:
                effective_host_id = ent.id

    eff_host_ent = Entity.query.get((game_token, effective_host_id))
    eff_host_type = eff_host_ent.entity_type if eff_host_ent else Entity.TYPENAME

    for source in recipe.sources:
        item = source.ingredient
        potential_owner_ids = []
        
        # Define search priorities based on storage type
        if item.storage_type == StorageType.UNIVERSAL:
            potential_owner_ids = [GENERAL_ID]
        elif item.storage_type == StorageType.LOCAL:
            if location_id:
                potential_owner_ids = [location_id]
        elif item.storage_type == StorageType.CARRIED:
            potential_owner_ids = [effective_host_id]
            if location_id and location_id != effective_host_id:
                potential_owner_ids.append(location_id)

        # Query existing piles
        valid_piles = Pile.query.filter(
            Pile.game_token == game_token,
            Pile.item_id == item.id,
            Pile.owner_id.in_(potential_owner_ids)
        ).all()

        total_qty = sum(p.quantity for p in valid_piles)
        
        # Pick a representative pile for the UI link
        # Priority: Host -> Location -> General
        representative_pile = None
        anticipated_owner_id = None
        anticipated_owner_type = None

        if valid_piles:
            def sort_priority(p):
                if p.owner_id == host_id: return 0
                if p.owner_id == location_id: return 1
                return 2
            sorted_piles = sorted(valid_piles, key=sort_priority)
            representative_pile = sorted_piles[0]
            anticipated_owner_id = representative_pile.owner_id

            # Fetch type for the URL builder
            ent = Entity.query.get((game_token, anticipated_owner_id))
            anticipated_owner_type = ent.entity_type if ent else Entity.TYPENAME
        else:
            # --- ANTICIPATED PILE LOGIC (No piles found) ---
            if item.storage_type == StorageType.UNIVERSAL:
                anticipated_owner_id = GENERAL_ID
                anticipated_owner_type = Entity.TYPENAME
            elif item.storage_type == StorageType.LOCAL:
                anticipated_owner_id = location_id
                anticipated_owner_type = Location.TYPENAME
            else: # CARRIED
                # If we are looking at a character, stay with that character
                # If we are looking at a location, stay with that location
                anticipated_owner_id = effective_host_id
                anticipated_owner_type = eff_host_type

        resolved_sources.append({
            'source_def': source,
            'item': item,
            'total_available': total_qty,
            'representative_pile': representative_pile,
            'anticipated_owner_id': anticipated_owner_id,
            'anticipated_owner_type': anticipated_owner_type,
            'all_piles': valid_piles
        })

    return resolved_sources

def ensure_owner_up_to_date(owner_id):
    """
    Checks if this owner is hosting an active production progress,
    and ticks it before we read their inventory.
    """
    prog = Progress.query.filter_by(
        game_token=g.game_token, 
        host_id=owner_id, 
        is_ongoing=True
    ).first()
    if prog:
        from .logic_progress import update_progress
        update_progress(prog.id)

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

def adjust_quantity(item_id, owner_id, delta, position=None, slot=None):
    """
    Increments or decrements an item quantity for a specific owner.
    - delta: positive to add, negative to subtract.
    - Returns: The new quantity.
    """
    pile = get_or_create_pile(item_id, owner_id, position, slot)
    
    # Check Item limits if increasing
    if delta > 0:
        item_def = Item.query.get((g.game_token, item_id))
        if item_def and item_def.q_limit > 0:
            if (pile.quantity + delta) > item_def.q_limit:
                logger.warning(f"Item {item_id} exceeds limit {item_def.q_limit}")
                pile.quantity = item_def.q_limit
                return pile.quantity

    old_qty = pile.quantity
    pile.quantity += delta
    
    if old_qty <= 0 and pile.quantity > 0:
        # gained an item for the first time
        check_item_unmasking(g.game_token, item_id)
    
    # Cleanup: remove empty rows to keep the DB small
    if pile.quantity == 0:
        safe_remove(pile)
        return 0.0
        
    return pile.quantity

def transfer_item(item_id, from_owner_id, to_owner_id, quantity, 
                  from_pos=None, to_pos=None, to_slot=None):
    """
    Moves quantity from one Entity to another.
    Handles 'Drop', 'Pick Up', 'Deposit', and 'Withdraw'.
    """
    if quantity <= 0:
        return False

    # 1. Check if source has enough
    source_pile = Pile.query.filter_by(
        game_token=g.game_token,
        item_id=item_id,
        owner_id=from_owner_id,
        position=from_pos
    ).first()

    if not source_pile or source_pile.quantity < quantity:
        logger.error(
            f"Transfer failed: Owner {from_owner_id} lacks"
            f" {quantity} of item {item_id}")
        return False

    # 2. Perform the logic atomicly
    adjust_quantity(item_id, from_owner_id, -quantity, position=from_pos)
    adjust_quantity(item_id, to_owner_id, quantity, position=to_pos, slot=to_slot)

    return True

# ------------------------------------------------------------------------
# Convenience Accessors
# ------------------------------------------------------------------------

def get_general_stock(item_id):
    """Helper to check the universal pile."""
    ensure_owner_up_to_date(GENERAL_ID)
    pile = Pile.query.filter_by(
        game_token=g.game_token,
        item_id=item_id,
        owner_id=GENERAL_ID
    ).first()
    return pile.quantity if pile else 0.0

def get_character_piles(char_id):
    """Returns all Pile objects carried by a character."""
    ensure_owner_up_to_date(char_id)
    return Pile.query.filter_by(
        game_token=g.game_token,
        owner_id=char_id
    ).all()

def get_location_piles(loc_id):
    """Returns all Pile objects sitting at a location (all grid positions)."""
    ensure_owner_up_to_date(loc_id)
    return Pile.query.filter_by(
        game_token=g.game_token,
        owner_id=loc_id
    ).all()

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
