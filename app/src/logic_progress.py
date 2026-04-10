import math
import logging
from datetime import datetime, timezone
from flask import g, session
from app.models import (
    db, Entity, Item, Character, Pile, Progress,
    Recipe, RecipeSource, RecipeByproduct, AttribVal,
    GENERAL_ID, StorageType)
from app.utils import format_num
from app.src.logic_piles import adjust_quantity
from app.src.logic_user_interaction import add_message
from app.src.logic_event import check_triggers, TriggerException
from app.src.logic_production import (
    can_perform_recipe, resolve_recipe_sources,
    get_production_target, execute_production)

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

    # 1. Calculate timing
    if recipe.instant:
        raise Exception("Expected a recipe that uses duration.")
    elapsed = get_elapsed_seconds(progress)
    total_potential_batches = math.floor(elapsed / recipe.rate_duration)
    new_batches = total_potential_batches - progress.batches_processed
    
    if new_batches <= 0:
        return

    # 2. Check for random interrupts
    try:
        check_triggers(progress.host, batches=new_batches)
    except TriggerException as e:
        progress.is_ongoing = False
        db.session.commit()
        raise e 

    # 3. Determine Context
    ctx_id = None
    host_ent = Entity.query.get((game_token, progress.host_id))
    if host_ent.entity_type == 'character':
        char = Character.query.get((game_token, progress.host_id))
        ctx_id = char.location_id
    elif host_ent.entity_type == 'location':
        ctx_id = host_ent.id
    elif progress.host_id == GENERAL_ID:
        ctx_id = session.get('old_loc_id')

    # 4. Delegate physical production
    logger.info(f"[TICK] Host:{progress.host_id} Recipe:{recipe.id} batches:{new_batches}")
    actual_done, halt_reason = execute_production(
        game_token, progress.host_id, recipe, batches=new_batches, context_id=ctx_id
    )

    # 5. Update Progress State
    progress.batches_processed += actual_done
    if halt_reason:
        progress.is_ongoing = False
        progress.stop_time = datetime.now()
        add_message(game_token, f"Production stopped: {halt_reason}")

    db.session.commit()
    return halt_reason

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
