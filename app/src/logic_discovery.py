import logging
from app.models import db, Item, Recipe, RecipeSource, Pile

logger = logging.getLogger(__name__)

def check_item_unmasking(game_token, item_id):
    """
    Unmasks items that depend on the provided item_id.
    Called whenever an item's quantity increases for the first time.
    """
    item = Item.query.get((game_token, item_id))
    if not item or item.counted_for_unmasking:
        return

    # Mark this item as proven so we don't process it repeatedly
    # Rules: It must be visible to the player AND they must have some of it.
    if not item.counted_for_unmasking and not item.masked:
        total_qty = db.session.query(db.func.sum(Pile.quantity))\
            .filter_by(game_token=game_token, item_id=item_id).scalar() or 0
        if total_qty > 0:
            item.counted_for_unmasking = True
            db.session.commit()
            logger.info(f"Item proven: {item.name}")

    # Find all masked items that use this item in a recipe
    if item.counted_for_unmasking:
        dependent_sources = RecipeSource.query.filter_by(
            game_token=game_token, item_id=item_id).all()
        
        for ds in dependent_sources:
            recipe = Recipe.query.get((game_token, ds.recipe_id))
            target_item = Item.query.get((game_token, recipe.product_id))

            if target_item and target_item.masked:
                # 3. Check if ALL sources for this specific recipe are proven
                if can_unmask_item(game_token, target_item):
                    logger.info(f"Unmasking discovered item: {target_item.name}")
                    target_item.masked = False
                    db.session.commit()

def can_unmask_item(game_token, item):
    """Returns True if at least one recipe for the item has all sources proven."""
    for recipe in item.recipes:
        all_sources_proven = True
        for source in recipe.sources:
            ingredient = Item.query.get((game_token, source.item_id))
            if not ingredient.counted_for_unmasking:
                all_sources_proven = False
                break
            
        if all_sources_proven:
            return True
    return False

def run_discovery_scan(game_token):
    """
    Scans every item in the game to sync the discovery state.
    Used after scenario loads or manual configuration edits.
    """
    items = Item.query.filter_by(game_token=game_token).all()
    for item in items:
        check_item_unmasking(game_token, item.id)

