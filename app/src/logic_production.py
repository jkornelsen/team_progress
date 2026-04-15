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

def find_best_host(recipe, char_id=None, loc_id=None):
    """
    Determines the host according to priority with strict channel checks.
    Enforces Storage-Type-Specific Priority to prevent General host 
    from splitting wood or crafting local items.
    """
    game_token = g.game_token
    product = Item.query.get((game_token, recipe.product_id))
    if not product:
        return None

    # Pre-calculate viability for each available channel
    
    # Character (Local Context)
    can_char = False
    if char_id:
        can_char, _ = can_perform_recipe(
            char_id, recipe, loc_id=loc_id,
            limit_to_channel=Character.TYPENAME)

    # Location (Ground only)
    can_loc = False
    if loc_id:
        can_loc, _ = can_perform_recipe(
            loc_id, recipe, limit_to_channel=Location.TYPENAME)

    # PRIORITY LOGIC
    # Branch 1: Product is Universal (Currency, Global Upgrades, etc.)
    if product.storage_type == StorageType.UNIVERSAL:
        # General (Universal only)
        # We only check this for universal products.
        can_gen, _ = can_perform_recipe(
            GENERAL_ID, recipe, limit_to_channel=GENERAL_ID)
        
        # Priority: System Efficiency (General Storage) -> Salience (Active Character)
        if can_gen: return GENERAL_ID
        if can_char: return char_id
        if can_loc: return loc_id
    
    # Branch 2: Product is Carried or Local (Tools, Resources, Structures)
    else:
        # Priority: Actor Presence -> Workshop Presence
        # General host (Host ID 1) is STRICTLY FORBIDDEN for non-universal items.
        if can_char: return char_id
        if can_loc: return loc_id

    return None
    
def get_production_target(item, host_id, char_id=None, loc_id=None):
    """
    Determines where a produced item should be placed based on its StorageType.
    
    Returns: The Entity ID of the container, or None if the combination is invalid.
    """
    game_token = g.game_token

    # 1. Universal items ALWAYS go to General Storage (ID 1)
    if item.storage_type == StorageType.UNIVERSAL:
        return GENERAL_ID
    
    # 2. Security Check: The General Storage (Host ID 1) is a singleton logic
    # runner that only handles Universal products. It cannot "own" local items.
    # This prevents Host 1 from splitting wood into a specific location.
    if host_id == GENERAL_ID:
        logger.warning(
            f"Blocked General host from producing non-universal item {item.id}")
        return None

    # 3. Handle Local/Stationary Items (Workshops, Chests)
    if item.storage_type == StorageType.LOCAL:
        # Context is paramount. If hosted by character, goes to their current floor.
        ent = Entity.query.get((game_token, host_id))
        if ent and ent.entity_type == Location.TYPENAME:
            return host_id
        if ent and ent.entity_type == Character.TYPENAME:
            char = Character.query.get((game_token, host_id))
            return char.location_id
        return char_id or loc_id

    # 4. Handle Carried/Portable Items (Inventory)
    # Carried items go to the Host if the host is a character, 
    # otherwise they land at the location (context).
    ent = Entity.query.get((game_token, host_id))
    if ent and ent.entity_type == Character.TYPENAME:
        return host_id

    return char_id or loc_id or host_id

def resolve_recipe_sources(
        host_id, recipe, char_id=None, loc_id=None, limit_to_channel=None):
    """
    Find where ingredients are located.
    Respects StorageType restrictions and visibility channels.
    """
    game_token = g.game_token
    resolved_sources = []
    
    # Determine Search Horizon
    search_ids = []
    
    if limit_to_channel == GENERAL_ID:
        search_ids = [GENERAL_ID]
    elif limit_to_channel == Character.TYPENAME:
        search_ids = [host_id]
        if loc_id: search_ids.append(loc_id)
    elif limit_to_channel == Location.TYPENAME:
        search_ids = [host_id]
    else:
        # Greedy Mode (Default)
        search_set = {GENERAL_ID}
        if host_id: search_set.add(host_id)
        if char_id: search_set.add(char_id)
        if loc_id: search_set.add(loc_id)
        search_ids = list(search_set)

    char_pos = None
    if host_id != GENERAL_ID:
        char = Character.query.get((game_token, host_id))
        if char:
            char_pos = char.position

    for source in recipe.sources:
        item = source.ingredient
        
        # Define search priorities based on storage type
        if item.storage_type == StorageType.UNIVERSAL:
            potential_owner_ids = [GENERAL_ID]
        elif item.storage_type == StorageType.LOCAL:
            potential_owner_ids = [loc_id] if loc_id else []
        elif item.storage_type == StorageType.CARRIED:
            # Look in the character's bag, or on the ground at the location
            potential_owner_ids = []
            if host_id and host_id != GENERAL_ID:
                potential_owner_ids.append(host_id)
            if char_id and char_id not in potential_owner_ids:
                potential_owner_ids.append(char_id)
            if loc_id and loc_id not in potential_owner_ids:
                potential_owner_ids.append(loc_id)

        potential_owner_ids = [
            eid for eid in potential_owner_ids if eid in search_ids]

        # Query existing piles
        all_piles = Pile.query.filter(
            Pile.game_token == game_token,
            Pile.item_id == item.id,
            Pile.owner_id.in_(potential_owner_ids)
        ).all()

        # Adjacency check for grid-based locations
        valid_piles = []
        for p in all_piles:
            if p.position and char_pos:
                if is_adjacent(char_pos, p.position):
                    valid_piles.append(p)
            else:
                valid_piles.append(p)

        total_qty = sum(p.quantity for p in valid_piles)
        
        # Determine anticipated target for UI help
        representative_pile = None
        if valid_piles:
            def sort_priority(p):
                if p.owner_id == host_id: return 0
                if p.owner_id == loc_id: return 1
                return 2
            sorted_piles = sorted(valid_piles, key=sort_priority)
            representative_pile = sorted_piles[0]
            owner_id = representative_pile.owner_id
            ent = Entity.query.get((game_token, owner_id))
            owner_type = ent.entity_type if ent else Entity.TYPENAME
        else:
            owner_id = GENERAL_ID if item.storage_type == StorageType.UNIVERSAL else host_id
            owner_type = Entity.TYPENAME

        resolved_sources.append({
            'source_def': source,
            'item': item,
            'total_available': total_qty,
            'representative_pile': representative_pile,
            'anticipated_owner_id': owner_id,
            'anticipated_owner_type': owner_type
        })

    return resolved_sources

def can_perform_recipe(
        host_id, recipe, batches=1, char_id=None, loc_id=None, limit_to_channel=None):
    """
    Validates if a host can perform a recipe. 
    This is now the primary gatekeeper for the General Host restriction.
    """
    game_token = g.game_token

    # 0. Storage Channel Check
    item_def = Item.query.get((game_token, recipe.product_id))
    target_id = get_production_target(item_def, host_id, char_id, loc_id)
    if not target_id:
        return False, "Invalid host for this item type."

    # 1. Output Limit
    if item_def.q_limit > 0:
        current_pile = Pile.query.filter_by(
            game_token=game_token, owner_id=target_id, item_id=recipe.product_id
        ).first()
        if current_pile and current_pile.quantity >= item_def.q_limit:
            return False, "Storage limit reached."

    # 2. Ingredient Availability
    resolved = resolve_recipe_sources(
        host_id, recipe, char_id, loc_id, limit_to_channel)
    for res in resolved:
        required = res['source_def'].q_required * batches
        if res['total_available'] < required:
            return False, f"Missing {res['item'].name}."

    # 3. Attribute Requirements
    relevant_entities = {GENERAL_ID, host_id, char_id, loc_id}
    for req in recipe.attrib_reqs:
        req_met = False
        for eid in relevant_entities:
            if not eid: continue
            av = AttribVal.query.filter_by(
                game_token=game_token, subject_id=eid, attrib_id=req.attrib_id).first()
            if av and req.in_range(av.value):
                req_met = True
                break
        if not req_met:
            return False, f"Requires {req.attrib.name} {req.range_display}"

    return True, ""

def execute_production(host_id, recipe, batches=1, char_id=None, loc_id=None):
    """Mechanically executes production batches and applies changes."""
    actual_batches_done = 0
    halt_reason = None

    for _ in range(batches):
        possible, reason = can_perform_recipe(
            host_id, recipe, 1, char_id, loc_id)
        if not possible:
            halt_reason = reason
            break

        # Consume
        resolved = resolve_recipe_sources(
            host_id, recipe, char_id=char_id, loc_id=loc_id)
        for res in resolved:
            if not res['source_def'].preserve:
                adjust_quantity(res['item'].id, res['anticipated_owner_id'], -res['source_def'].q_required)

        # Produce
        product_item = Item.query.get((g.game_token, recipe.product_id))
        target_id = get_production_target(
            product_item, host_id, char_id, loc_id)
        adjust_quantity(recipe.product_id, target_id, recipe.rate_amount)
        
        for bp in recipe.byproducts:
            bp_item = Item.query.get((g.game_token, bp.item_id))
            bp_target_id = get_production_target(
                bp_item, host_id, char_id, loc_id)
            adjust_quantity(bp.item_id, bp_target_id, bp.rate_amount)

        actual_batches_done += 1

    # Log who did the production
    if actual_batches_done > 0:
        game_token = g.game_token
        host_ent = Entity.query.get((game_token, host_id))
        if host_id == GENERAL_ID:
            host_info = "GENERAL/SYSTEM"
        elif host_ent:
            host_info = f"{host_ent.entity_type.upper()} '{host_ent.name}' (ID:{host_id})"
        else:
            host_info = f"UNKNOWN ID:{host_id}"
        logger.info(
            f"[PRODUCTION] Host: {host_info} | "
            f"Result: +{recipe.rate_amount * actual_batches_done:g} {recipe.product.name} "
            f"({actual_batches_done} batches)"
        )

    return actual_batches_done, halt_reason
