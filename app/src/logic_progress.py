import math
import logging
from datetime import datetime, timezone
from flask import g, session
from sqlalchemy import text
import zlib
from app.models import (
    db, Entity, Item, Location, Character, Pile, Progress,
    Recipe, RecipeSource, RecipeByproduct, AttribVal,
    GENERAL_ID, StorageType)
from app.utils import ContextIds
from .logic_piles import adjust_quantity
from .logic_production import can_perform_recipe, execute_production, STALLED
from .logic_user_interaction import add_message

logger = logging.getLogger(__name__)

def tick_all_active(messages_host_id=None):
    """
    Ticks every active production record in the current game session.
    
    - messages_host_id: If provided, returns a list of halt 
      reasons specifically for this host.
    """
    game_token = g.game_token

    # DB lock to prevent concurrent access to this game token
    lock_id = zlib.adler32(game_token.encode())
    try:
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:id)"), 
            {"id": lock_id}
        )
    except Exception as e:
        logger.exception(e)

    all_active_records = Progress.query.filter_by(game_token=game_token).all()
    
    # --- PHASE 1: PREPARATION ---
    CHUNK_SIZE = 8
    work_items = []
    max_catchup_time = 0

    for p in all_active_records:
        recipe = p.recipe
        if not recipe:
            logger.warning(f"No Recipe for Progress {p.id} found. Deleting.")
            db.session.delete(p)
            continue
        if recipe.instant:
            logger.warning(f"Progress {p.id} points to instant recipe {recipe.id}.")
            continue

        # Find the time debt
        elapsed = get_elapsed_seconds(p)
        total_potential = math.floor(elapsed / recipe.rate_duration)
        new_batches = total_potential - p.batches_processed
        
        if new_batches > 0:
            catchup_seconds = new_batches * recipe.rate_duration
            max_catchup_time = max(max_catchup_time, catchup_seconds)

            work_items.append({
                'progress': p,
                'recipe': recipe,
                'total_remaining': new_batches,
                'chunk_size': math.ceil(new_batches / CHUNK_SIZE),
                'catching_up': new_batches > 2,
                'halt_reason': None,
                'ctx': ContextIds(
                    owner_id=p.owner_id, 
                    host_id=p.host_id, 
                    char_id=p.char_id, 
                    loc_id=p.loc_id
                )
            })

    # --- PHASE 2: THE INTERLEAVED WAVES ---
    # We do an extra wave to allow dependencies to catch up
    for wave in range(CHUNK_SIZE + 1):
        any_work_done_this_wave = False
        
        for work in work_items:
            # Skip if already finished or halted
            if work['total_remaining'] <= 0 or work['halt_reason']:
                continue

            # Determine size of this chunk
            to_do = min(work['chunk_size'], work['total_remaining'])
            
            # Make the change
            p = work['progress']
            actual, reason = execute_production(
                p.host_id, 
                work['recipe'], 
                p.owner_id, 
                work['ctx'], 
                batches=to_do,
                catching_up=work['catching_up'],
                stop_at=p.stop_at
            )
            
            # Update tracking
            work['total_remaining'] -= actual
            p.batches_processed += actual
            
            if actual > 0:
                any_work_done_this_wave = True
            elif reason == "Stalled":
                logger.debug(f"Item {work['recipe'].product_id} waiting.")
            elif reason:
                # If it halted, record why
                work['halt_reason'] = reason

        # Optimization: If the whole world is stuck, stop looping
        if not any_work_done_this_wave:
            break

    # --- PHASE 3: LOGGING & COMMITS ---
    if max_catchup_time >= 600:
        hours = int(max_catchup_time // 3600)
        minutes = int((max_catchup_time % 3600) // 60)
        time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        add_message(f"Caught up after {time_str}")

    halt_messages = []
    for work in work_items:
        p = work['progress']
        halt_reason = work['halt_reason']
        
        # Check future viability
        if not halt_reason:
            possible, reason = can_perform_recipe(
                p.host_id, work['recipe'], p.owner_id, work['ctx'])
            if not possible:
                halt_reason = reason

        # Handle deletion for halted work
        if halt_reason:
            # Capture needed data into local variables while p is still valid
            prod_name = p.product.name if p.product else "Unknown Item"
            p_host_id = p.host_id
            p_id = p.id
            stop_msg = f"Production of {prod_name} halted: {halt_reason}"
            logger.info(f"[PRODUCTION STOPPED] Host:{p_host_id} | {stop_msg}")
            add_message(stop_msg)
            if p_host_id == messages_host_id:
                halt_messages.append(halt_reason)
            db.session.delete(p)

    # Once commit is called, the advisory lock is automatically released.
    db.session.commit()
    return halt_messages

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

def start_production(host_id, recipe_id, owner_id, ctx, stop_at=None):
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
            game_token=game_token, host_id=host_id
        ).first()
        if active_job:
            if active_job.product_id == recipe.product_id:
                return False, f"{host_entity.name} is already working on this."
            return False, f"{host_entity.name} is busy working on {active_job.product.name}."
    else:
        # Concurrent: General and Location can host one job per product
        active_job = Progress.query.filter_by(
            game_token=game_token,
            host_id=host_id, 
            product_id=recipe.product_id
        ).first()
        if active_job:
            here = ' here' if host_entity.entity_type == Location.TYPENAME else ''
            return False, f"{active_job.product.name} is already being produced{here}."

    # Find or Create Record
    progress = Progress.query.filter_by(
        game_token=game_token,
        host_id=host_id,
        product_id=recipe.product_id
    ).first()

    if not progress:
        progress = Progress(
            game_token=game_token,
            recipe_id=recipe.id,
            product_id=recipe.product_id,
            owner_id=owner_id,
            host_id=host_id,
            char_id=ctx.char_id,
            loc_id=ctx.loc_id,
            stop_at=stop_at)
        db.session.add(progress)
    else:
        progress.stop_at = stop_at

    progress.start_time = datetime.now()
    progress.batches_processed = 0
    
    db.session.commit()
    return True, "Production started."

def stop_production(host_id, product_id):
    """Pauses production and performs one last catch-up check."""
    game_token = g.game_token
    progress = Progress.query.filter_by(
        game_token=game_token, host_id=host_id, product_id=product_id
    ).first()

    if progress:
        tick_all_active() # Final catch up
        still_exists = db.session.query(Progress).filter_by(id=progress.id).first()
        if still_exists:
            logger.info(
                f"[PRODUCTION MANUAL STOP] Host:{host_id}"
                f" | Product:{product_id}")
            db.session.delete(still_exists)
        db.session.commit()
        return True
    return False
