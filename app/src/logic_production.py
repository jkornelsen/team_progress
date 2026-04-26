import math
import logging
from datetime import datetime, timezone
from flask import g, session
from app.models import (
    db, Entity, Item, Location, Character, Pile, Progress,
    Recipe, AttribVal, GENERAL_ID, StorageType)
from .logic_piles import adjust_quantity
from .logic_navigation import is_adjacent
from .logic_user_interaction import add_message

logger = logging.getLogger(__name__)

def find_best_host(recipe, owner_id, ctx):
    """
    Determines the host according to priority with strict channel checks.
    Enforces Storage-Type-Specific Priority to prevent General host 
    from splitting wood or crafting local items.
    """
    logger.debug(
        f"find_best_host() | Product:{recipe.product_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    game_token = g.game_token
    product = Item.query.get((game_token, recipe.product_id))

    # 1. THE MACHINE CHECK (Highest Priority)
    # If the recipe requires a LOCAL crafting station marked as automated, 
    # the Location must be the host.
    if recipe.is_location_hosted:
        return ctx.loc_id

    # 2. UNIVERSAL PRODUCT BRANCH (Currencies, Global Upgrades)
    if product.storage_type == StorageType.UNIVERSAL:
        # Priority A: General Host (System)
        # We use this if ingredients/stats are all in the global bank.
        if can_perform_recipe(GENERAL_ID, recipe, owner_id, ctx)[0]:
            return GENERAL_ID

        # Priority B: Character (Personal Trigger)
        # If the bank is missing ingredients, but the character has them.
        if ctx.char_id and can_perform_recipe(ctx.char_id, recipe, owner_id, ctx)[0]:
            return ctx.char_id

        # Priority C: Location (Environment/Passive)
        # If no character is there.
        if ctx.loc_id and can_perform_recipe(ctx.loc_id, recipe, owner_id, ctx)[0]:
            return ctx.loc_id

        # Fallback for UI (Error reporting)
        return GENERAL_ID

    # 3. PHYSICAL PRODUCT BRANCH (Tools, Resources, Structures)
    else:
        return ctx.char_id

def can_perform_recipe(host_id, recipe, target_owner_id, ctx, batches=1):
    """
    Validates if a host can perform a recipe. 
    Checks Storage limits, Ingredients, and Attributes.
    """
    logger.debug(
        f"can_perform_recipe() | Product:{recipe.product_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")
    game_token = g.game_token
    if not host_id:
        return False, "No appropriate host."

    # Character hosts can only use nearby piles
    host_pos = None
    host_ent = Entity.query.get((game_token, host_id))
    if host_ent and host_ent.entity_type == Character.TYPENAME:
        host_pos = host_ent.position

    # 1. Output Limit
    if recipe.product.q_limit > 0:
        query = Pile.query.filter_by(
            game_token=game_token,
            item_id=recipe.product_id,
            owner_id=target_owner_id
        )
        if host_pos is not None:
            query = query.filter_by(position=host_pos)
        current_pile = query.first()

        if current_pile and current_pile.quantity >= recipe.product.q_limit:
            return False, "Storage limit reached."

    # 2. Ingredient Availability
    resolved = resolve_recipe_sources(host_id, recipe, ctx)
    for res in resolved:
        required = res['source_def'].q_required * batches
        if res['total_available'] < required:
            verb = "Missing"
            if res['total_available'] > 0:
                verb = "Need More"
            return False, f"{verb} {res['item'].name}"

    # 3. Attribute Requirements
    scope = get_host_scope(host_id, ctx)

    for req in recipe.attrib_reqs:
        req_met = False
        for eid in scope:
            av = AttribVal.query.filter_by(
                game_token=game_token, subject_id=eid, attrib_id=req.attrib_id).first()
            if av and req.in_range(av.value):
                req_met = True
                break
        if not req_met:
            return False, f"Requires {req.attrib.name} {req.range_display}"

    return True, ""

def get_host_scope(host_id, ctx):
    """
    Returns the list of Entity IDs a host is allowed to interact with.
    - General Host: Only sees the global bank (GENERAL_ID).
    - Location Host: Sees the bank + items/stats at that specific location.
    - Character Host: Sees the bank + the location + their own inventory/stats.
    """
    game_token = g.game_token
    if host_id == GENERAL_ID:
        return [GENERAL_ID]

    host_ent = Entity.query.get((game_token, host_id))
    if host_ent and host_ent.entity_type == Character.TYPENAME:
        # Actor: Greedy scope
        return ctx.unique_ids(GENERAL_ID, host_id, ctx.loc_id)
    
    # Machine/Environment: Local scope
    return ctx.unique_ids(GENERAL_ID, host_id)

def resolve_recipe_sources(host_id, recipe, ctx):
    """
    - General Host (System): Only sees Universal items in General Storage.
    - Location Host (Machine): Sees Universal items + items on its own floor.
    - Character Host (Actor): Sees Universal items + items on the floor + their own bag.
    """
    game_token = g.game_token
    resolved_sources = []
    
    # Determine Search Scope based on Host Identity
    host_ent = None
    host_pos = None
    if host_id == GENERAL_ID:
        search_ids = [GENERAL_ID]
    else:
        host_ent = Entity.query.get((game_token, host_id))
        if host_ent and host_ent.entity_type == Character.TYPENAME:
            # Characters can reach into their own pockets, the floor, and global bank
            search_ids = ctx.unique_ids(GENERAL_ID, host_id, ctx.loc_id)
            host_pos = host_ent.position
        else:
            # Locations (Machines) can reach the floor and global bank
            search_ids = ctx.unique_ids(GENERAL_ID, ctx.loc_id)
    logger.debug(f"Search IDs: {search_ids}")

    for source in recipe.sources:
        item = source.ingredient
        
        # Define search priorities based on storage type
        if item.storage_type == StorageType.UNIVERSAL:
            potential_owner_ids = [GENERAL_ID]
        elif item.storage_type == StorageType.LOCAL:
            potential_owner_ids = ctx.unique_ids(ctx.loc_id)
        elif item.storage_type == StorageType.CARRIED:
            # Look in the character's bag, or on the ground at the location
            potential_owner_ids = ctx.unique_ids(
                ctx.not_general(host_id),
                ctx.not_general(ctx.owner_id),
                ctx.char_id, ctx.loc_id)
        potential_owner_ids = [
            eid for eid in potential_owner_ids if eid in search_ids]
        logger.debug(
            f"Checking Item:{item.name} (Type:{item.storage_type})"
            f" in Owners:{potential_owner_ids}")

        # Query existing piles
        all_piles = Pile.query.filter(
            Pile.game_token == game_token,
            Pile.item_id == item.id,
            Pile.owner_id.in_(potential_owner_ids)
        ).all()

        # Adjacency check for grid-based locations
        valid_piles = []
        for p in all_piles:
            if p.position and host_pos:
                if is_adjacent(host_pos, p.position):
                    valid_piles.append(p)
            else:
                valid_piles.append(p)

        total_qty = sum(p.quantity for p in valid_piles)
        
        # Determine anticipated target for UI help
        representative_pile = None
        if valid_piles:
            def sort_priority(p):
                if p.owner_id == host_id: return 0
                if p.owner_id == ctx.loc_id: return 1
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

def execute_production(host_id, recipe, target_owner_id, ctx, batches=1):
    """Executes production batches and applies changes."""
    logger.debug(
        f"execute_production() Host:{host_id} | Product:{recipe.product_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    if batches <= 0:
        return 0, None

    halt_reason = None
    possible, reason = can_perform_recipe(
        host_id, recipe, target_owner_id, ctx, batches)
    if not possible:
        return 0, reason

    # Consume
    resolved = resolve_recipe_sources(host_id, recipe, ctx)
    for res in resolved:
        if not res['source_def'].preserve:
            adjust_quantity(
                res['item'].id,
                res['anticipated_owner_id'],
                -(res['source_def'].q_required * batches))

    # Produce
    adjust_quantity(
        recipe.product_id, target_owner_id, recipe.rate_amount * batches)
    
    for bp in recipe.byproducts:
        bp_target_id = get_byproduct_target(
            bp.item_id, target_owner_id, host_id, ctx)
        adjust_quantity(bp.item_id, bp_target_id, bp.rate_amount * batches)

    # Log who did the production
    game_token = g.game_token
    gain_qty = recipe.rate_amount * batches
    host_ent = Entity.query.get((game_token, host_id))
    log_msg = f"{gain_qty} {recipe.product.name}"
    if host_id == GENERAL_ID:
        host_info = "GENERAL/SYSTEM"
        log_msg = f"{log_msg} gained."
    elif host_ent:
        host_info = f"{host_ent.entity_type.upper()} '{host_ent.name}' (ID:{host_id})"
        if host_ent.entity_type == Character.TYPENAME:
            log_msg = f"{host_ent.name} produced {log_msg}."
        elif host_ent.entity_type == Location.TYPENAME:
            log_msg = f"{log_msg} produced at {host_ent.name}."
    else:
        host_info = f"UNKNOWN ID:{host_id}"
    add_message(log_msg)
    logger.info(
        f"[PRODUCTION] Host: {host_info} | "
        f"Result: +{gain_qty:g} {recipe.product.name} "
        f"({batches} batches)"
    )
    return batches, None

def get_byproduct_target(item_id, main_target_id, host_id, ctx):
    """
    Determines where secondary items go (the owner) based on StorageType.
    """
    game_token = g.game_token

    item = Item.query.get((g.game_token, item_id))
    if item.storage_type == StorageType.UNIVERSAL:
        return GENERAL_ID
    
    # If it's a tool/carried item, try to give it to the host or character
    if item.storage_type == StorageType.CARRIED:
        return ctx.best_char_id or host_id
        
    # Otherwise, default to the same place as the main product
    return main_target_id
