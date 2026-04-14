import math
import logging
from datetime import datetime, timezone
from flask import g, session
from app.models import (
    db, Entity, Item, Location, Character, Pile, Progress,
    Recipe, RecipeSource, RecipeByproduct, AttribVal,
    GENERAL_ID, StorageType)
from app.utils import format_num
from app.src.logic_piles import adjust_quantity
from app.src.logic_navigation import is_adjacent
from app.src.logic_user_interaction import add_message
from app.src.logic_event import check_triggers, TriggerException

logger = logging.getLogger(__name__)

def get_production_target(item, host_id, context_id=None):
    """
    Determines where a produced item should be placed based on its StorageType.
    """
    game_token = g.game_token

    if item.storage_type == StorageType.UNIVERSAL:
        return GENERAL_ID
    
    if item.storage_type == StorageType.LOCAL:
        # 1. Try to find the location via the Host (e.g., if Suzy is the host)
        host = Entity.query.get((game_token, host_id))
        if host and host.entity_type == Character.TYPENAME:
            char = Character.query.get((game_token, host_id))
            if char.location_id:
                return char.location_id
        
        # 2. If the Host is already a location, use it
        if host and host.entity_type == Location.TYPENAME:
            raise Exception(
                f"Logical Error: {host.name} cannot be a production host.")

        # 3. Fallback to context_id (usually the 'old_loc_id' from session)
        if context_id:
            return context_id
        
    # Default for CARRIED items: they land in the Host's inventory
    return host_id

def resolve_recipe_sources(host_id, recipe, context_id=None):
    """
    Find where ingredients are located.

    Returns a list of resolved source data for a recipe.
    Respects StorageType restrictions.
    'context_id' allows looking up local/carried items relative to a 
    specific actor, even if the 'host_id' is General Storage.
    """
    game_token = g.game_token
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
            effective_host_id = char.id
        elif ent.entity_type == Location.TYPENAME:
            if not location_id: 
                location_id = ent.id
            if effective_host_id == GENERAL_ID:
                effective_host_id = ent.id

    eff_host_ent = Entity.query.get((game_token, effective_host_id))
    eff_host_type = eff_host_ent.entity_type if eff_host_ent else Entity.entity_type

    char_pos = None
    if effective_host_id != GENERAL_ID:
        char = Character.query.get((game_token, effective_host_id))
        if char:
            char_pos = char.position

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
        all_piles = Pile.query.filter(
            Pile.game_token == game_token,
            Pile.item_id == item.id,
            Pile.owner_id.in_(potential_owner_ids)
        ).all()

        # Position Dependency Filter
        # If the location has a grid, and the pile has a position, 
        # and the host is a character, they must be adjacent.
        valid_piles = []
        for p in all_piles:
            # Piles in a character's backpack (no position) are always valid.
            # If the pile is on the ground (has position), check adjacency.
            if p.position and char_pos:
                if is_adjacent(char_pos, p.position):
                    valid_piles.append(p)
            else:
                valid_piles.append(p)

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
            anticipated_owner_type = ent.entity_type if ent else Entity.entity_type
        else:
            # --- ANTICIPATED PILE LOGIC (No piles found) ---
            if item.storage_type == StorageType.UNIVERSAL:
                anticipated_owner_id = GENERAL_ID
                anticipated_owner_type = Entity.entity_type
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

def can_perform_recipe(host_id, recipe, batches=1, context_id=None):
    """
    Checks if the host has enough ingredients and meets attribute requirements,
    and hasn't hit item limits.
    Returns (bool, reason_string)
    """
    game_token = g.game_token

    # Where the item is going
    item_def = Item.query.get((game_token, recipe.product_id))
    target_id = get_production_target(item_def, host_id, context_id)
    if not target_id:
        return False, "No valid storage target found."

    # 1. Check Output Limit
    if item_def and item_def.q_limit > 0:
        current_pile = Pile.query.filter_by(
            game_token=game_token, owner_id=target_id, item_id=recipe.product_id
        ).first()
        current_qty = current_pile.quantity if current_pile else 0.0
        
        if current_qty >= item_def.q_limit:
            logger.debug(f"{current_qty} above storage limit")
            return (
                False,
                "Storage limit reached"
                f" ({format_num(item_def.q_limit)} {item_def.name})")
    logger.debug(f"{current_qty} within limit")

    # 2. Check Sources (Ingredients)
    resolved = resolve_recipe_sources(host_id, recipe, context_id)
    for res in resolved:
        required = res['source_def'].q_required * batches
        if res['total_available'] < required:
            source_item = res['item']
            needed = required - res['total_available']
            logger.debug(f"Missing {needed} {source_item.name}")
            return False, f"Missing {format_num(needed)} {source_item.name} (Need {format_num(required)})"

    # 3. Check Attribute Requirements
    # Check attributes on all relevant entities that could contribute to the recipe
    # Include host entity and context entity (if different)
    relevant_entities = set()
    if host_id and host_id != GENERAL_ID:
        relevant_entities.add(host_id)
    if context_id and context_id != GENERAL_ID:
        relevant_entities.add(context_id)
    
    # If we are in General Storage and have no context, 
    # we should check General Storage (ID 1) itself for attributes.
    if not relevant_entities:
        relevant_entities.add(GENERAL_ID)

    for req in recipe.attrib_reqs:
        req_met = False
        for entity_id in relevant_entities:
            # Get the current attribute value for this entity
            attrib_val = AttribVal.query.filter_by(
                game_token=game_token, subject_id=entity_id, attrib_id=req.attrib_id
            ).first()
            current_val = attrib_val.value if attrib_val else 0.0
            
            if req.in_range(current_val):
                req_met = True
                break
        
        if not req_met:
            if req.attrib.is_binary:
                if req.min_val > 0:
                    return False, f"Requires {req.attrib.name} (must be enabled)"
                else:
                    return False, f"Requires {req.attrib.name} (must be disabled)"
            else:
                return False, f"{req.attrib.name} {req.range_display} required"

    logger.debug(f"Can produce recipe")
    return True, ""


def execute_production(host_id, recipe, batches=1, context_id=None):
    """
    The core 'Mechanical' function. 
    Runs up to 'batches' times. Halts early if resources run out.
    Calculates completed batches, consumes sources, and produces items.
    
    Returns: (actual_batches_done, halt_reason)
    """
    from .logic_piles import adjust_quantity
    game_token = g.game_token

    actual_batches_done = 0
    halt_reason = None

    for _ in range(batches):
        # 1. Check if this specific batch can be performed
        possible, reason = can_perform_recipe(
            host_id, recipe, context_id=context_id)
        
        if not possible:
            halt_reason = reason
            break

        # 2. Consume Sources
        resolved = resolve_recipe_sources(host_id, recipe, context_id=context_id)
        for res in resolved:
            source_def = res['source_def']
            if not source_def.preserve:
                target_owner_id = res['anticipated_owner_id']
                adjust_quantity(res['item'].id, target_owner_id, -source_def.q_required)

        # 3. Produce Output
        product_item = Item.query.get((game_token, recipe.product_id))
        main_target_id = get_production_target(product_item, host_id, context_id)
        adjust_quantity(recipe.product_id, main_target_id, recipe.rate_amount)
        
        # 4. Produce Byproducts
        for byproduct in recipe.byproducts:
            bp_item = Item.query.get((game_token, byproduct.item_id))
            bp_target_id = get_production_target(bp_item, host_id, context_id)
            adjust_quantity(byproduct.item_id, bp_target_id, byproduct.rate_amount)

        actual_batches_done += 1

    return actual_batches_done, halt_reason
