import logging
from app.models import db, Item, Recipe, RecipeSource, Pile

logger = logging.getLogger(__name__)

def check_item_unmasking(game_token, item_id):
    """
    Recursively unmasks items that depend on the provided item_id.
    Called whenever an item's quantity increases for the first time.
    """
    # 1. Find the item that was just updated/discovered
    source_item = Item.query.get((game_token, item_id))
    if not source_item or source_item.counted_for_unmasking:
        return

    # Mark this item as "known" so we don't process it repeatedly
    source_item.counted_for_unmasking = True
    db.session.commit()

    # 2. Find all masked items that use this item in a recipe
    # We query RecipeSource to find dependent recipes
    dependent_sources = RecipeSource.query.filter_by(game_token=game_token, item_id=item_id).all()
    
    for ds in dependent_sources:
        recipe = Recipe.query.get((game_token, ds.recipe_id))
        target_item = Item.query.get((game_token, recipe.product_id))

        if target_item and target_item.masked:
            # 3. Check if ALL sources for this specific recipe are "known"
            # Known = (unmasked) OR (has quantity > 0)
            if can_unmask_item(game_token, target_item):
                logger.info(f"Unmasking discovered item: {target_item.name}")
                target_item.masked = False
                db.session.commit()
                
                # 4. RECURSION: Since target_item is now unmasked, 
                # it might unmask something even further down the chain
                check_item_unmasking(game_token, target_item.id)

def can_unmask_item(game_token, item):
    """Returns True if at least one recipe for the item has all sources known."""
    for recipe in item.production_recipes:
        all_sources_known = True
        for source in recipe.sources:
            ingredient = Item.query.get((game_token, source.item_id))
            
            # Is the ingredient already unmasked or do we have it in stock?
            has_stock = Pile.query.filter(
                Pile.game_token == game_token,
                Pile.item_id == source.item_id,
                Pile.quantity > 0
            ).first() is not None
            
            if ingredient.masked and not has_stock:
                all_sources_known = False
                break
        
        if all_sources_known:
            return True
    return False
