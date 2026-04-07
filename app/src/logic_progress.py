import math
import logging
from datetime import datetime, timezone
from flask import g
from app.models import (
    db, Pile, Progress, Recipe, RecipeSource, RecipeByproduct, Item,
    GENERAL_ID)
from app.utils import format_num
from app.src.logic_piles import adjust_quantity
from app.src.logic_user_interaction import add_message
from app.src.logic_event import check_triggers, TriggerException

logger = logging.getLogger(__name__)

def get_elapsed_seconds(progress):
    """Calculates seconds since production started or last update."""
    if not progress.start_time:
        return 0.0
    
    now = datetime.now(timezone.utc)
    # Ensure start_time is offset-aware if it isn't already
    start = progress.start_time.replace(tzinfo=timezone.utc) if progress.start_time.tzinfo is None else progress.start_time
    
    return (now - start).total_seconds()

def can_perform_recipe(game_token, host_id, recipe, batches=1):
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

    # 2. Check Ingredients (Sources)
    for source in recipe.sources:
        pile = Pile.query.filter_by(
            game_token=game_token, owner_id=host_id, item_id=source.item_id
        ).first()
        
        current_qty = pile.quantity if pile else 0.0
        required = source.q_required * batches
        
        if current_qty < required:
            source_item = Item.query.get((game_token, source.item_id))
            needed = required - current_qty
            return False, f"Missing {format_num(needed)} {source_item.name} (Need {format_num(required)})"

    return True, ""

def update_progress(progress_id):
    """
    The main tick function.
    Calculates completed batches, consumes sources, and produces items.
    """
    game_token = g.game_token
    progress = Progress.query.get((game_token, progress_id))
    
    if not progress or not progress.is_ongoing or not progress.recipe_id:
        return

    if not progress.start_time:
        # If it's ongoing but has no start time, something is wrong. 
        # Reset it to now to prevent a crash.
        progress.start_time = datetime.now(timezone.utc)
        db.session.commit()
        return

    recipe = Recipe.query.get((game_token, progress.recipe_id))
    if not recipe:
        return

    elapsed = get_elapsed_seconds(progress)
    
    # Calculate how many total batches SHOULD have been done by now
    total_potential_batches = math.floor(elapsed / recipe.rate_duration)
    
    # How many new batches occurred since the last time we checked?
    new_batches = total_potential_batches - progress.batches_processed
    
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

    # Process batches one by one (or in a chunk) to check for resource exhaustion
    actual_batches_done = 0
    for _ in range(new_batches):
        possible, reason = can_perform_recipe(game_token, progress.host_id, recipe)
        if not possible:
            # Stop production if we run out of stuff
            progress.is_ongoing = False
            progress.stop_time = datetime.now(timezone.utc)
            add_message(game_token, f"Production stopped: {reason}")
            break
        
        # 1. Consume Sources
        for source in recipe.sources:
            if not source.preserve:
                adjust_quantity(source.item_id, progress.host_id, -source.q_required)
        
        # 2. Produce Output
        adjust_quantity(recipe.product_id, progress.host_id, recipe.rate_amount)
        
        # 3. Produce Byproducts
        for byproduct in recipe.byproducts:
            adjust_quantity(byproduct.item_id, progress.host_id, byproduct.rate_amount)
            
        actual_batches_done += 1

    # Update state
    progress.batches_processed += actual_batches_done
    db.session.commit()

def start_production(host_id, recipe_id):
    """Initializes a Progress record for an Entity."""
    game_token = g.game_token
    recipe = Recipe.query.get((game_token, recipe_id))
    
    # Check if we can even start the first batch
    possible, reason = can_perform_recipe(game_token, host_id, recipe)
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
    progress.start_time = datetime.now(timezone.utc)
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
        progress.stop_time = datetime.now(timezone.utc)
        db.session.commit()
        return True
    return False
