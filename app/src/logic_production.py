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

def can_perform_recipe(
        host_id, recipe, target_owner_id, ctx, batches=1,
        limit_to_channel=None):
    """
    Validates if a host can perform a recipe. 
    Checks Storage limits, Ingredients, and Attributes.
    """
    game_token = g.game_token

    logger.debug(
        f"can_perform_recipe() | Product:{recipe.product_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    # Character hosts can only use nearby piles
    host_pos = None
    if host_id != GENERAL_ID: # slightly more efficient
        char = Character.query.get((game_token, host_id))
        if char:
            host_pos = char.position

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
    resolved = resolve_recipe_sources(
        host_id, recipe, ctx, limit_to_channel)
    for res in resolved:
        required = res['source_def'].q_required * batches
        if res['total_available'] < required:
            return False, f"Missing {res['item'].name}."

    # 3. Attribute Requirements
    relevant_ids = ctx.unique_ids(
        GENERAL_ID, host_id, ctx.owner_id, ctx.char_id, ctx.loc_id)
    for req in recipe.attrib_reqs:
        req_met = False
        for eid in relevant_ids:
            if not eid: continue
            av = AttribVal.query.filter_by(
                game_token=game_token, subject_id=eid, attrib_id=req.attrib_id).first()
            if av and req.in_range(av.value):
                req_met = True
                break
        if not req_met:
            return False, f"Requires {req.attrib.name} {req.range_display}"

    return True, ""

def resolve_recipe_sources(host_id, recipe, ctx, limit_to_channel=None):
    """
    Find where ingredients are located.
    Respects StorageType restrictions and visibility channels.
    """
    game_token = g.game_token
    resolved_sources = []
    
    # Determine Search IDs
    if limit_to_channel == GENERAL_ID:
        search_ids = [GENERAL_ID]
    elif limit_to_channel == Character.TYPENAME:
        search_ids = ctx.unique_ids(host_id, ctx.char_id)
    elif limit_to_channel == Location.TYPENAME:
        search_ids = ctx.unique_ids(host_id, ctx.loc_id)
    else:
        # Greedy Mode (Default)
        search_ids = ctx.unique_ids(
            GENERAL_ID, host_id, ctx.owner_id, ctx.char_id, ctx.loc_id)
    logger.debug(f"Search IDs: {search_ids}")

    # Character hosts can only use nearby piles
    host_pos = None
    if host_id != GENERAL_ID: # slightly more efficient
        char = Character.query.get((game_token, host_id))
        if char:
            host_pos = char.position

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
    if not product:
        return None

    # Pre-calculate viability for each available channel
    
    # Character (Local Context)
    can_char = False
    if ctx.char_id:
        can_char, _ = can_perform_recipe(
            ctx.char_id, recipe, owner_id, ctx,
            limit_to_channel=Character.TYPENAME)

    # Location (Ground only)
    can_loc = False
    if ctx.loc_id:
        can_loc, _ = can_perform_recipe(
            ctx.loc_id, recipe, owner_id, ctx,
            limit_to_channel=Location.TYPENAME)

    # PRIORITY LOGIC
    # Branch 1: Product is Universal (Currency, Global Upgrades, etc.)
    if product.storage_type == StorageType.UNIVERSAL:
        # General (Universal only)
        # We only check this for universal products.
        can_gen, _ = can_perform_recipe(
            GENERAL_ID, recipe, owner_id, ctx,
            limit_to_channel=GENERAL_ID)
        
        # Priority: System Efficiency (General Storage) -> Salience (Active Character)
        if can_gen: return GENERAL_ID
        if can_char: return ctx.char_id
        if can_loc: return ctx.loc_id
        return GENERAL_ID
    
    # Branch 2: Product is Carried or Local (Tools, Resources, Structures)
    else:
        # Priority: Actor Presence -> Workshop Presence
        # General host (Host ID 1) is STRICTLY FORBIDDEN for non-universal items.
        if can_char: return ctx.char_id
        if can_loc: return ctx.loc_id
        return ctx.char_id or ctx.loc_id

def execute_production(host_id, recipe, target_owner_id, ctx, batches=1):
    """Executes production batches and applies changes."""
    logger.debug(
        f"execute_production() Host:{host_id} | Product:{recipe.product_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    actual_batches_done = 0
    halt_reason = None

    for _ in range(batches):
        possible, reason = can_perform_recipe(
            host_id, recipe, target_owner_id, ctx, 1)
        if not possible:
            halt_reason = reason
            break

        # Consume
        resolved = resolve_recipe_sources(host_id, recipe, ctx)
        for res in resolved:
            if not res['source_def'].preserve:
                adjust_quantity(
                    res['item'].id,
                    res['anticipated_owner_id'],
                    -res['source_def'].q_required)

        # Produce
        adjust_quantity(
            recipe.product_id, target_owner_id, recipe.rate_amount)
        
        for bp in recipe.byproducts:
            bp_target_id = get_byproduct_target(
                bp.item_id, target_owner_id, host_id, ctx)
            adjust_quantity(bp.item_id, bp_target_id, bp.rate_amount)

        actual_batches_done += 1

    # Log who did the production
    if actual_batches_done > 0:
        game_token = g.game_token
        gain_qty = recipe.rate_amount * actual_batches_done
        host_ent = Entity.query.get((game_token, host_id))
        log_msg = f"{gain_qty} {recipe.product.name}"
        if host_id == GENERAL_ID:
            host_info = "GENERAL/SYSTEM"
            log_msg = f"{log_msg} gained."
        elif host_ent:
            host_info = f"{host_ent.entity_type.upper()} '{host_ent.name}' (ID:{host_id})"
            if host_ent.entity_type == Character.TYPENAME:
                log_msg = f"{host_end.name} produced {log_msg}."
            elif host_ent.entity_type == Location.TYPENAME:
                log_msg = f"{log_msg} produced at {host_end.name}."
        else:
            host_info = f"UNKNOWN ID:{host_id}"
        add_message(log_msg)
        logger.info(
            f"[PRODUCTION] Host: {host_info} | "
            f"Result: +{gain_qty:g} {recipe.product.name} "
            f"({actual_batches_done} batches)"
        )

    return actual_batches_done, halt_reason

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
