import math
import logging
from datetime import datetime, timezone
from flask import g, session
from app.models import (
    db, Entity, Item, Location, Character, Pile, Progress,
    Recipe, AttribVal, GENERAL_ID, StorageType)
from app.utils import maskable_name
from .logic_piles import (
    adjust_quantity, get_accessible_quantity, get_quantity_limit)
from .logic_navigation import (
    is_adjacent, get_output_positions, find_best_output_pos)
from .logic_user_interaction import add_message

logger = logging.getLogger(__name__)

STALLED = "Stalled"

def find_best_host(recipe, owner_id, ctx):
    """
    Determines the host according to priority with strict channel checks.
    Enforces Storage-Type-Specific Priority to prevent General host 
    from splitting wood or crafting local items.
    """
    logger.debug(
        f"find_best_host() | Product:{recipe.product_id}"
        f" | is_location_hosted:{recipe.is_location_hosted}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    game_token = g.game_token
    product = db.session.get(Item, (game_token, recipe.product_id))

    # 1. THE MACHINE CHECK (Highest Priority)
    # If the recipe requires a LOCAL crafting station marked as automated, 
    # the Location must be the host.
    if recipe.is_location_hosted:
        return ctx.loc_id

    # 2. UNIVERSAL PRODUCT BRANCH (Currencies, Global Upgrades)
    if product.storage_type == StorageType.UNIVERSAL:
        # Priority A: General Host (System)
        # If ingredients/stats are all in the global bank.
        if has_ingredients(GENERAL_ID, recipe, owner_id, ctx)[0]:
            return GENERAL_ID

        # Priority B: Character (Personal Trigger)
        # If the bank is missing ingredients, but the character has them.
        if ctx.char_id and has_ingredients(
                ctx.char_id, recipe, owner_id, ctx)[0]:
            return ctx.char_id

        # Priority C: Location (Environment/Passive)
        # If no character is there.
        if ctx.loc_id and has_ingredients(
                ctx.loc_id, recipe, owner_id, ctx)[0]:
            return ctx.loc_id

        # Fallback for UI (Error reporting)
        return GENERAL_ID

    # 3. PHYSICAL PRODUCT BRANCH (Tools, Resources, Structures)
    logger.debug(
        f"find_best_host() returning | is_location_hosted:{recipe.is_location_hosted}"
        f" | storage_type:{product.storage_type}"
        f" | result:{ctx.char_id}")
    return ctx.char_id

def resolve_host_pos(host_id, recipe, sources=None):
    """Returns (loc_id, anchor_pos) for a host."""
    host_ent = db.session.get(Entity, (g.game_token, host_id))
    if not host_ent or host_ent.entity_type not in [
            Character.TYPENAME, Location.TYPENAME]:
        return None, None

    if host_ent.entity_type == Character.TYPENAME:
        return host_ent.location_id, host_ent.position

    anchor_pos = None
    if recipe.is_location_hosted and sources:
        for src in sources:
            if src['item'].storage_type == StorageType.LOCAL \
                    and src['best_pile']:
                anchor_pos = src['best_pile'].position
                break
    return host_ent.id, anchor_pos

def get_eligible_placements(recipe, target_owner_id, host_id, sources=None):
    """
    Returns the prioritized list of (owner_id, position) for yield storage.
    """
    game_token = g.game_token
    product = recipe.product
    placements = []

    if product.storage_type == StorageType.UNIVERSAL:
        return [(GENERAL_ID, None)]

    loc_id, anchor_pos = resolve_host_pos(host_id, recipe, sources)

    # Intended Target: Backpack
    if product.storage_type == StorageType.CARRIED:
        target_ent = db.session.get(Entity, (game_token, target_owner_id))
        if target_ent and target_ent.entity_type == Character.TYPENAME:
            # If a machine is at a Location, Character must be there too
            if loc_id:
                if target_ent.location_id == loc_id:
                    placements.append((target_owner_id, None))
                else:
                    # Character left the location
                    logger.info(f"Target {target_ent.name} left {loc_id}. Spilling to floor.")
            else:
                # No specific location (Universal Host), allow backpack delivery
                placements.append((target_owner_id, None))

    # Physical: Floor / Surroundings
    if loc_id:
        loc = db.session.get(Location, (game_token, loc_id))
        if loc.has_grid and anchor_pos:
            for cand in get_output_positions(loc, anchor_pos):
                placements.append((loc_id, cand))
        else:
            # No grid: spill to the location's "general" floor pile
            placements.append((loc_id, None))

    return placements

def get_placement_capacity(recipe, target_owner_id, host_id, sources=None):
    """
    Returns the total space available across all eligible output placements,
    expressed as a number of whole batches. Returns float('inf') if unlimited.
    """
    if recipe.rate_amount <= 0:
        return float('inf'), float('inf')

    game_token = g.game_token
    placements = get_eligible_placements(
        recipe, target_owner_id, host_id, sources)
    if not placements:
        return 0, 0.0
    owner_id, pos = placements[0]
    q_limit = get_quantity_limit(recipe.product_id, owner_id)
    if q_limit == 0:
        return float('inf'), float('inf')
    pile = Pile.query.filter_by(
        game_token=game_token, owner_id=owner_id,
        item_id=recipe.product_id, position=pos).first()
    current_qty = pile.quantity if pile else 0.0
    total_capacity = max(0.0, q_limit - current_qty)

    return math.floor(total_capacity / recipe.rate_amount), total_capacity

def has_ingredients(
        host_id, recipe, target_owner_id, ctx, batches=1,
        catching_up=False, sources=None, stop_at=None):
    """
    Checks if a host has the ingredients and attribute requirements to perform
    a recipe.
    """
    logger.debug(
        f"has_ingredients() Product:{recipe.product_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")
    game_token = g.game_token
    if not host_id:
        return False, "No appropriate host."

    # A Character host must still be at the location that any local
    # ingredient depends on
    host_ent = db.session.get(Entity, (game_token, host_id))
    if host_ent and host_ent.entity_type == Character.TYPENAME:
        needs_local = any(
            s.ingredient.storage_type == StorageType.LOCAL
            for s in recipe.sources)
        if needs_local and ctx.loc_id and host_ent.location_id != ctx.loc_id:
            return False, f"{host_ent.name} left the location."

    if stop_at is not None:
        current_qty = get_accessible_quantity(recipe.product_id, target_owner_id)
        if recipe.is_producer and current_qty >= stop_at:
            return False, f"Target {stop_at:g} reached"
        if recipe.is_consumer and current_qty <= stop_at:
            return False, f"Target {stop_at:g} reached"

    # Ingredient Availability
    if sources is None:
        sources = resolve_recipe_sources(host_id, recipe, ctx)
    for src in sources:
        source_def = src['source_def']
        required = source_def.q_required * (
            1 if source_def.preserve else batches)
        if src['total_available'] < required:
            if catching_up:
                is_being_produced = db.session.query(Progress.id).filter_by(
                    game_token=game_token, 
                    product_id=src['item'].id
                ).first() is not None
                if is_being_produced:
                    return False, STALLED
            verb = "Missing"
            if src['total_available'] > 0:
                verb = "Need More"
            return False, f"{verb} {maskable_name(src['item'])}"

    # Attribute Requirements
    scope = get_host_scope(host_id, ctx)
    for src in sources:
        if src['total_available'] > 0:
            scope.append(src['item'].id)

    for req in recipe.attrib_reqs:
        req_met = False
        for eid in scope:
            av = AttribVal.query.filter_by(
                game_token=game_token,
                subject_id=eid,
                attrib_id=req.attrib_id).first()
            if av and req.is_satisfied(av.value):
                req_met = True
                break
        if not req_met:
            return False, \
                f"Requires {maskable_name(req.attrib)} {req.display}"

    return True, ""

def can_perform_recipe(
        host_id, recipe, target_owner_id, ctx, batches=1,
        catching_up=False, sources=None, stop_at=None):
    """Can this host perform the recipe."""
    if host_id is None:
        return False, "No appropriate host."
    _, total_capacity = get_placement_capacity(
        recipe, target_owner_id, host_id, sources)
    if total_capacity <= 0:
        return False, "Storage limit reached"
    return has_ingredients(
        host_id, recipe, target_owner_id, ctx,
        batches=batches, catching_up=catching_up,
        sources=sources, stop_at=stop_at)

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

    host_ent = db.session.get(Entity, (game_token, host_id))

    # Character: Can see their own bags, the floor they stand on, and the bank.
    if host_ent and host_ent.entity_type == Character.TYPENAME:
        return ctx.unique_ids(GENERAL_ID, host_id, ctx.loc_id)
    
    # Machine/Environment: Local scope
    return ctx.unique_ids(GENERAL_ID, host_id)

def resolve_recipe_sources(host_id, recipe, ctx):
    """
    Determines where ingredients are pulled from based on host identity.
    """
    logger.debug(
        f"resolve_recipe_sources() | host_id:{host_id}"
        f" | host_id type:{type(host_id)}"
        f" | GENERAL_ID:{GENERAL_ID}"
        f" | host_id==GENERAL_ID:{host_id == GENERAL_ID}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")
    game_token = g.game_token
    resolved_sources = []
    
    # Determine Search Scope based on Host Identity
    host_ent = None
    host_pos = None # The physical tile where production is centered
    if host_id == GENERAL_ID:
        search_ids = [GENERAL_ID]
    else:
        host_ent = db.session.get(Entity, (game_token, host_id))
        if host_ent and host_ent.entity_type == Character.TYPENAME:
            # Characters can reach into their own pockets, the floor, and global bank
            search_ids = ctx.unique_ids(GENERAL_ID, host_id, ctx.loc_id)
            host_pos = host_ent.position
        else:
            # Locations (Machines) can reach the floor and global bank
            search_ids = ctx.unique_ids(GENERAL_ID, host_id)
    logger.debug(f"Search IDs: {search_ids}")

    for source in recipe.sources:
        item = source.ingredient
        
        # Determine pile based on Storage Type
        if item.storage_type == StorageType.UNIVERSAL:
            potential_owner_ids = [GENERAL_ID]
        elif item.storage_type == StorageType.LOCAL:
            if host_ent and host_ent.entity_type == Location.TYPENAME:
                potential_owner_ids = [host_id]
            else:
                potential_owner_ids = ctx.unique_ids(ctx.loc_id)
        elif item.storage_type == StorageType.CARRIED:
            if host_ent and host_ent.entity_type == Location.TYPENAME:
                potential_owner_ids = [host_id]
            else:
                potential_owner_ids = ctx.unique_ids(
                    ctx.not_general(host_id),
                    ctx.not_general(ctx.owner_id),
                    ctx.char_id,
                    ctx.loc_id)
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
        best_pile = None
        if valid_piles:
            def sort_priority(p):
                if p.owner_id == host_id: return 0
                if p.owner_id == ctx.loc_id: return 1
                return 2
            sorted_piles = sorted(valid_piles, key=sort_priority)
            best_pile = sorted_piles[0]
            owner_id = best_pile.owner_id
            ent = db.session.get(Entity, (game_token, owner_id))
            owner_type = ent.entity_type if ent else Entity.TYPENAME
        else:
            owner_id = GENERAL_ID if item.storage_type == StorageType.UNIVERSAL else host_id
            owner_type = Entity.TYPENAME

        resolved_sources.append({
            'source_def': source,
            'item': item,
            'total_available': total_qty,
            'best_pile': best_pile,
            'all_candidate_piles': valid_piles,
            'anticipated_owner_id': owner_id,
            'anticipated_owner_type': owner_type
        })

    return resolved_sources

def execute_production(
        host_id, recipe, target_owner_id, ctx, batches=1,
        catching_up=False, stop_at=None):
    """Executes production batches and applies changes.
    @param target_owner_id: the initial intent
    """
    logger.debug(
        f"execute_production() Host:{host_id} | Product:{recipe.product_id}"
        f" | Char:{ctx.char_id} | Loc:{ctx.loc_id}")

    if batches <= 0:
        return 0, None
    game_token = g.game_token
    host_ent = db.session.get(Entity, (game_token, host_id))
    if not host_ent: return 0, "Host not found."

    # Validate if we can perform at least ONE
    sources = resolve_recipe_sources(host_id, recipe, ctx)

    # Clamp batches to what output placements can actually absorb.
    # Do this before can_perform_recipe so that a nearly-full pile doesn't
    # block a partial batch — we produce as much as fits, consuming full
    # source quantities per batch (no partial ingredient splits).
    capacity_batches, total_capacity = get_placement_capacity(
        recipe, target_owner_id, host_id, sources)
    if capacity_batches == float('inf'):
        pass  # unlimited
    elif capacity_batches >= 1:
        batches = min(batches, capacity_batches)
    else:
        # Less than one full batch of space — check if any space at all
        if total_capacity <= 0:
            return 0, "Storage limit reached"
        # else: allow 1 batch; the produce loop will deposit what fits

    # Validate ingredients/attributes (limit check handled by capacity clamp above)
    possible, reason = has_ingredients(
        host_id, recipe, target_owner_id, ctx, batches=1,
        catching_up=catching_up, sources=sources)
    if not possible:
        return 0, reason

    # Calculate the ceiling based on current ingredients and stop_at

    net_change = recipe.net_product_change
    current_qty = get_accessible_quantity(recipe.product_id, target_owner_id)

    if stop_at is not None:
        if recipe.is_producer:
            if current_qty >= stop_at:
                return 0, f"Reached target of {stop_at:g}"
            remaining_needed = stop_at - current_qty
            batches_allowed = math.ceil(remaining_needed / net_change)
            batches = min(batches, batches_allowed)

        elif recipe.is_consumer:
            if current_qty <= stop_at:
                return 0, f"Dropped to target of {stop_at:g}"
            remaining_to_drain = current_qty - stop_at
            batches_allowed = math.ceil(remaining_to_drain / abs(net_change))
            batches = min(batches, batches_allowed)

    if catching_up and batches > 1:
        max_possible = batches
        for src in sources:
            source_def = src['source_def']
            if not source_def.preserve and source_def.q_required > 0:
                # How many batches can this specific ingredient support?
                limit = math.floor(
                    src['total_available'] / source_def.q_required)
                if limit < max_possible:
                    max_possible = limit
        
        # Check Output Limits
        q_limit = get_quantity_limit(recipe.product_id, target_owner_id)
        for limit in (q_limit, stop_at):
            if limit and net_change:
                remaining = limit - current_qty
                if net_change * remaining > 0: # Same sign and neither 0

                    batches_limit = math.floor(remaining / net_change)
                    if remaining % net_change:
                        batches_limit += 1

                    if batches_limit < max_possible:
                        max_possible = batches_limit

        batches = max(1, max_possible)

    # Final verification for the (potentially adjusted) batch count
    if batches > 1:
        possible, reason = has_ingredients(
            host_id, recipe, target_owner_id, ctx, batches,
            catching_up=catching_up, sources=sources)
        if not possible:
            return 0, reason

    # Consume
    net_product_delta = 0.0
    for src in sources:
        if not src['source_def'].preserve:
            debt = src['source_def'].q_required * batches
            
            # Drain from candidates one by one
            for p in src['all_candidate_piles']:
                if debt <= 0: break
                
                # Try to take the debt from this specific pile
                unpaid = adjust_quantity(
                    src['item'].id, p.owner_id, -debt, p.position)

                if src['item'].id == recipe.product_id:
                    drained = debt - abs(unpaid)
                    net_product_delta -= drained

                debt = abs(unpaid) 

    # Produce
    _, anchor_pos = resolve_host_pos(host_id, recipe, sources)
    placements = get_eligible_placements(
        recipe, target_owner_id, host_id, sources)
    amount_to_place = recipe.rate_amount * batches
    if placements:
        owner_id, pos = placements[0]
        remainder = adjust_quantity(
            recipe.product_id, owner_id, amount_to_place, position=pos)
        placed = amount_to_place - max(0.0, remainder)
    else:
        placed = 0.0
    net_product_delta += placed

    for bp in recipe.byproducts:
        bp_target_id = get_byproduct_target(
            bp.item_id, target_owner_id, host_id, ctx)
        adjust_quantity(bp.item_id, bp_target_id, bp.rate_amount * batches,
            position=anchor_pos)

    # Log the production
    if net_product_delta < 0:
        verb = "consumed"
    else:
        verb = "gained" if host_id == GENERAL_ID else "produced"
    log_msg = f"{abs(net_product_delta):g} {maskable_name(recipe.product)}"
    if host_id == GENERAL_ID:
        log_msg = f"{log_msg} {verb}"
    elif host_ent.entity_type == Character.TYPENAME:
        log_msg = f"{host_ent.name} {verb} {log_msg}"
    else:
        log_msg = f"{log_msg} {verb} at {host_ent.name}"
    add_message(log_msg)

    return batches, None

def get_byproduct_target(item_id, main_target_id, host_id, ctx):
    """
    Determines where secondary items go (the owner).
    """
    game_token = g.game_token

    item = db.session.get(Item, (g.game_token, item_id))
    if item.storage_type == StorageType.UNIVERSAL:
        return GENERAL_ID
    
    host_ent = db.session.get(Entity, (g.game_token, host_id))
    if host_ent.entity_type == Character.TYPENAME:
        return main_target_id if main_target_id != GENERAL_ID else host_id

    if host_ent.entity_type == Location.TYPENAME:
        return host_id

    # Universal hosted but not universal byproduct so put it wherever we can
    return next(ctx.unique_ids(
        ctx.not_general(ctx.owner_id),
        ctx.char_id,
        ctx.loc_id), None)
