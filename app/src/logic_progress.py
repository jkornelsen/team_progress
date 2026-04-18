import math
import logging
from datetime import datetime, timezone
from flask import g, session
from app.models import (
    db, Entity, Item, Location, Character, Pile, Progress,
    Recipe, RecipeSource, RecipeByproduct, AttribVal,
    GENERAL_ID, StorageType)
from app.utils import ContextIds
from app.src.logic_piles import adjust_quantity
from app.src.logic_event import check_triggers, TriggerException
from app.src.logic_production import (
    can_perform_recipe, execute_production)

logger = logging.getLogger(__name__)

def update_progress(progress_id):
    """
    The main tick function. Handles timing and triggers, 
    then delegates work to logic_production.
    """
    progress = Progress.query.get(progress_id)
    if not progress or not progress.is_ongoing or not progress.recipe_id:
        return

    game_token = g.game_token
    recipe = Recipe.query.get((game_token, progress.recipe_id))
    if not recipe:
        return
    if recipe.instant:
        raise Exception("Expected a recipe that uses duration.")

    # 1. Determine Context (For ingredient/attribute lookups)
    ctx_ids = ContextIds(
        owner_id=progress.owner_id, 
        host_id=progress.host_id, 
        char_id=progress.char_id, 
        loc_id=progress.loc_id
    )

    # 2. Timing Calculation
    elapsed = get_elapsed_seconds(progress)
    total_potential_batches = math.floor(elapsed / recipe.rate_duration)
    new_batches = total_potential_batches - progress.batches_processed
    
    actual_done = 0
    halt_reason = None

    # 3. Process time-elapsed batches
    if new_batches > 0:
        try:
            check_triggers(progress.host, batches=new_batches)
        except TriggerException as e:
            progress.is_ongoing = False
            db.session.commit()
            raise e 

        logger.debug(
            f"[TICK] Host:{progress.host_id} Recipe:{recipe.id} batches:{new_batches}")
        actual_done, halt_reason = execute_production(
            progress.host_id, recipe, progress.owner_id, ctx_ids, new_batches)
        
        progress.batches_processed += actual_done

    # 4. Check future viability
    # If we didn't already halt due to execute_production,
    # check if the NEXT batch is possible.
    if not halt_reason:
        possible, reason = can_perform_recipe(
            progress.host_id, recipe, progress.owner_id, ctx_ids)
        if not possible:
            halt_reason = reason

    # 5. Handle Haltung
    if halt_reason:
        logger.info(f"[HALT] Stopping Recipe {recipe.id}: {halt_reason}")
        progress.is_ongoing = False
        progress.stop_time = datetime.now()

    db.session.commit()
    return halt_reason

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

def tick_all_active(messages_host_id=None):
    """
    Ticks every active production record in the current game session.
    
    - messages_host_id: If provided, returns a list of halt 
      reasons specifically for this host.
    """
    game_token = g.game_token

    # Query all records that are currently marked as ongoing
    all_active = Progress.query.filter_by(
        game_token=game_token, is_ongoing=True).all()
    
    halt_messages = []
    for p in all_active:
        reason = update_progress(p.id)
        if reason and messages_host_id and p.host_id == messages_host_id:
            halt_messages.append(reason)
            
    return halt_messages

def start_production(host_id, recipe_id, owner_id, ctx):
    """Initializes a Progress record for an Entity."""
    game_token = g.game_token
    recipe = Recipe.query.get((game_token, recipe_id))
    host_entity = Entity.query.get((game_token, host_id))
    
    # Check if we can even start the first batch
    possible, reason = can_perform_recipe(
        host_id, recipe, owner_id, ctx)
    if not possible:
        return False, reason

    # Concurrency Checks
    if host_entity.entity_type == Character.TYPENAME:
        # Singleton: Characters can only do one thing at a time
        active_job = Progress.query.filter_by(
            game_token=game_token, host_id=host_id, is_ongoing=True
        ).first()
        if active_job:
            if active_job.product_id == recipe.product_id:
                return False, f"{host_entity.name} is already working on this."
            return False, f"{host_entity.name} is busy working on {active_job.product.name}."
    else:
        # Concurrent: General and Location can host one job per product
        active_job = Progress.query.filter_by(
            game_token=game_token, host_id=host_id, 
            product_id=recipe.product_id, is_ongoing=True
        ).first()
        if active_job:
            here = ' here' if host_entity.entity_type == Location.TYPENAME else ''
            return False, f"{active_job.product.name} is already being produced{here}."

    # Find or Create Record
    progress = Progress.query.filter_by(
        game_token=game_token, host_id=host_id, product_id=recipe.product_id
    ).first()

    if not progress:
        progress = Progress(
            game_token=game_token,
            recipe_id=recipe.id,
            product_id=recipe.product_id,
            owner_id=owner_id,
            host_id=host_id,
            char_id=ctx.char_id,
            loc_id=ctx.loc_id)
        db.session.add(progress)

    progress.start_time = datetime.now()
    progress.batches_processed = 0
    progress.is_ongoing = True
    progress.stop_time = None
    
    db.session.commit()
    return True, "Production started."

def stop_production(host_id, product_id):
    """Pauses production and performs one last catch-up check."""
    game_token = g.game_token
    progress = Progress.query.filter_by(
        game_token=game_token, host_id=host_id, product_id=product_id
    ).first()

    if progress and progress.is_ongoing:
        # Final catch up
        update_progress(progress.id)
        
        progress.is_ongoing = False
        progress.stop_time = datetime.now()  # Naive datetime
        db.session.commit()
        return True
    return False
