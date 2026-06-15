import logging
from app.models import db, Item, Recipe, RecipeSource, Pile

logger = logging.getLogger(__name__)

def check_item_unmasking(game_token, item_id, was_gained=False):
    """
    Tick-safe discovery logic.
    Updates the state of the item provided and checks its immediate dependents.
    """
    item = db.session.get(Item, (game_token, item_id))
    if not item:
        return

    # 1. Reveal the item itself if it was just gained
    if item.masked and was_gained:
        item.masked = False
        db.session.flush()
        logger.info(f"Item discovered via gain: {item.name}")

    # 2. Update the 'Proven' flag. 
    # An item is proven if it's visible AND the player has some.
    if not item.masked and not item.counted_for_unmasking:
        total_qty = db.session.query(db.func.sum(Pile.quantity))\
            .filter_by(game_token=game_token, item_id=item_id).scalar() or 0
        if total_qty > 0:
            item.counted_for_unmasking = True
            db.session.flush()
            logger.info(f"Item proven: {item.name}")

    # 3. Check items that REQUIRE this item.
    # We only do this if this item is now 'Proven'.
    if item.counted_for_unmasking:
        # Find all recipes that use this item as an ingredient
        dependent_sources = RecipeSource.query.filter_by(
            game_token=game_token, item_id=item_id).all()
        
        for ds in dependent_sources:
            recipe = db.session.get(Recipe, (game_token, ds.recipe_id))
            if not recipe: continue
            
            target_item = db.session.get(Item, (game_token, recipe.product_id))

            # If the product of that recipe is still masked, see if it can be revealed
            if target_item and target_item.masked:
                if can_unmask_item(game_token, target_item):
                    logger.info(f"Unmasking dependent: {target_item.name}")
                    target_item.masked = False
                    # We do NOT call check_item_unmasking recursively here.
                    # This prevents the 'chain reaction' unlock.
    
    # Use flush to stay safe for the tick loop
    db.session.flush()

def can_unmask_item(game_token, item):
    """Returns True if at least one recipe for the item has all sources available."""
    for recipe in item.recipes:
        all_sources_available = True
        for source in recipe.sources:
            ingred = db.session.get(Item, (game_token, source.item_id))
            
            # A source is available if it's not masked AND the player has had some.
            # We check both the flag AND the actual quantity for safety.
            if ingred.masked:
                all_sources_available = False
                break
            
            if not ingred.counted_for_unmasking:
                total_qty = db.session.query(db.func.sum(Pile.quantity))\
                    .filter_by(game_token=game_token, item_id=ingred.id).scalar() or 0
                if total_qty <= 0:
                    all_sources_available = False
                    break
            
        if all_sources_available:
            return True
    return False

def run_discovery_scan(game_token):
    """
    Thorough scan used when loading a file or saving the editor.
    This IS allowed to loop because it's not called during a production tick.
    """
    items = Item.query.filter_by(game_token=game_token).all()
    # Loop multiple times to catch multi-stage reveals (A reveals B reveals C)
    for _ in range(5):
        changes_made = False
        for item in items:
            old_masked = item.masked
            check_item_unmasking(game_token, item.id)
            if item.masked != old_masked:
                changes_made = True
        if not changes_made:
            break
    db.session.commit() # Scan is safe to commit
