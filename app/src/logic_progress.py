import math
import logging
from datetime import datetime, timezone
from flask import g
from app.models import (
    db, Entity, Item, Character, Pile, Progress,
    Recipe, RecipeSource, RecipeByproduct, AttribVal, GENERAL_ID)
from app.utils import format_num
from app.src.logic_piles import adjust_quantity, resolve_recipe_sources
from app.src.logic_user_interaction import add_message
from app.src.logic_event import check_triggers, TriggerException

logger = logging.getLogger(__name__)

def get_elapsed_seconds(progress):
    """Calculates seconds since production started or last update."""
    if not progress.start_time:
        return 0.0
    
    # Use naive datetime for comparison (consistent with DB storage)
    now = datetime.now()
    start = progress.start_time
    
    # Handle both naive and timezone-aware start times by stripping timezone if present
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    
    elapsed_seconds = (now - start).total_seconds()
    logger.debug(f"get_elapsed_seconds: now={now}, start={start}, elapsed={elapsed_seconds}s")
    return elapsed_seconds

def can_perform_recipe(game_token, host_id, recipe, batches=1, context_id=None):
    """
    Checks if the host has enough ingredients and hasn't hit item limits.
    Returns (bool, reason_string)
    """
    # 1. Check Output Limit
    item_def = Item.query.get((game_token, recipe.product_id))
    if item_def and item_def.q_limit > 0:
        # Get current quantity in this host's pile
        current_pile = Pile.query.filter_by(
            game_token=game_token, owner_id=host_id, item_id=recipe.product_id
        ).first()
        current_qty = current_pile.quantity if current_pile else 0.0
        
        if current_qty >= item_def.q_limit:
            return (
                False,
                "Storage limit reached"
                f" ({format_num(item_def.q_limit)} {item_def.name})")

    # 2. Check Sources (Ingredients)
    resolved = resolve_recipe_sources(game_token, host_id, recipe, context_id)
    for res in resolved:
        required = res['source_def'].q_required * batches
        if res['total_available'] < required:
            source_item = res['item']
            needed = required - res['total_available']
            return False, f"Missing {format_num(needed)} {source_item.name} (Need {format_num(required)})"

    # 3. Check Attribute Requirements
    # Check attributes on all relevant entities that could contribute to the recipe
    # Include host entity and context entity (if different)
    relevant_entities = set()
    if host_id and host_id != GENERAL_ID:
        relevant_entities.add(host_id)
    if context_id and context_id != GENERAL_ID:
        relevant_entities.add(context_id)
    
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

    return True, ""

def update_progress(progress_id):
    """
    The main tick function.
    Calculates completed batches, consumes sources, and produces items.
    """
    progress = Progress.query.get((progress_id))
    
    if not progress or not progress.is_ongoing or not progress.recipe_id:
        return

    if not progress.start_time:
        # If it's ongoing but has no start time, something is wrong. 
        # Reset it to now to prevent a crash.
        progress.start_time = datetime.now()  # Naive datetime
        db.session.commit()
        return

    game_token = g.game_token
    recipe = Recipe.query.get((game_token, progress.recipe_id))
    if not recipe:
        return

    elapsed = get_elapsed_seconds(progress)
    
    # Calculate how many total batches SHOULD have been done by now
    total_potential_batches = math.floor(elapsed / recipe.rate_duration)
    
    # How many new batches occurred since the last time we checked?
    new_batches = total_potential_batches - progress.batches_processed
    
    logger.info(f"update_progress: elapsed={elapsed}s, rate_duration={recipe.rate_duration}s, "
                f"total_potential={total_potential_batches}, processed={progress.batches_processed}, "
                f"new_batches={new_batches}")
    
    if new_batches <= 0:
        return

    try:
        # Check if the location or the item itself triggers something
        check_triggers(progress.host, batches=new_batches)
    except TriggerException as e:
        # STOP production immediately so the user has to resolve the event
        progress.is_ongoing = False
        db.session.commit()
        raise e # Re-raise for the route to catch

    # Identify Context (So we know where the host is standing)
    # This ensures resolve_recipe_sources finds the "Farm" while Suzy is the host.
    host_ent = Entity.query.get((game_token, progress.host_id))
    ctx_id = None
    if host_ent and host_ent.entity_type == 'character':
        char = Character.query.get((game_token, progress.host_id))
        ctx_id = char.location_id

    # Process batches one by one (or in a chunk) to check for resource exhaustion
    actual_batches_done = 0
    for _ in range(new_batches):
        possible, reason = can_perform_recipe(
            game_token, progress.host_id, recipe, context_id=ctx_id)
        if not possible:
            progress.is_ongoing = False
            progress.stop_time = datetime.now()  # Naive datetime
            add_message(game_token, f"Production stopped: {reason}")
            break
        
        resolved = resolve_recipe_sources(
            game_token, progress.host_id, recipe, context_id=ctx_id)
        for res in resolved:
            source_def = res['source_def']
            if not source_def.preserve:
                target_owner_id = res['anticipated_owner_id']
                adjust_quantity(res['item'].id, target_owner_id, -source_def.q_required)

        # 2. Produce Output
        adjust_quantity(recipe.product_id, progress.host_id, recipe.rate_amount)
        
        # 3. Produce Byproducts
        for byproduct in recipe.byproducts:
            adjust_quantity(byproduct.item_id, progress.host_id, byproduct.rate_amount)
            
        actual_batches_done += 1

    # Update state
    progress.batches_processed += actual_batches_done
    db.session.commit()

def start_production(host_id, recipe_id, context_id=None):
    """Initializes a Progress record for an Entity."""
    game_token = g.game_token
    recipe = Recipe.query.get((game_token, recipe_id))
    
    # Check if we can even start the first batch
    possible, reason = can_perform_recipe(
        game_token, host_id, recipe, context_id=context_id)
    if not possible:
        return False, reason

    # Find or create progress record
    progress = Progress.query.filter_by(
        game_token=game_token, host_id=host_id
    ).first()

    if not progress:
        progress = Progress(game_token=game_token, host_id=host_id)
        db.session.add(progress)

    progress.recipe_id = recipe_id
    progress.start_time = datetime.now()  # Naive datetime, consistent with DB storage
    progress.batches_processed = 0
    progress.is_ongoing = True
    progress.stop_time = None
    
    db.session.commit()
    return True, "Production started."

def stop_production(host_id):
    """Pauses production and performs one last catch-up check."""
    game_token = g.game_token
    progress = Progress.query.filter_by(
        game_token=game_token, host_id=host_id
    ).first()

    if progress and progress.is_ongoing:
        # Final catch up
        update_progress(progress.id)
        
        progress.is_ongoing = False
        progress.stop_time = datetime.now()  # Naive datetime
        db.session.commit()
        return True
    return False
