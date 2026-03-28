import unittest
from .testing_utils import BaseTestCase
from app.models import (
    db, Item, Entity, Recipe, RecipeSource, Pile, GENERAL_ID)
from app.src.progress_logic import can_perform_recipe
from app.src.serialization import import_from_dict

class TestItemIntegrated(BaseTestCase):

    def test_jti_inheritance(self):
        """Verify that creating an Item creates the underlying Entity registry row."""
        new_item = Item(
            id=100, 
            game_token=self.game_token, 
            name="Steel Ingot", 
            entity_type="item",
            storage_type="universal"
        )
        db.session.add(new_item)
        db.session.commit()

        # Check that it exists in the subclass table
        item_row = Item.query.get((100, self.game_token))
        self.assertEqual(item_row.name, "Steel Ingot")

        # Check that it exists in the base entity table (Inheritance check)
        entity_row = Entity.query.get((100, self.game_token))
        self.assertIsNotNone(entity_row)
        self.assertEqual(entity_row.entity_type, "item")

    def test_recipe_dependency_logic(self):
        """Test if production logic correctly sees ingredients across the relational tables."""
        # 1. Setup: Create Item A (Tool), Item B (Resource), and Item C (Product)
        tool = Item(id=10, game_token=self.game_token, name="Hammer", storage_type="universal")
        resource = Item(id=11, game_token=self.game_token, name="Wood", storage_type="universal")
        product = Item(id=12, game_token=self.game_token, name="Plank", storage_type="universal")
        db.session.add_all([tool, resource, product])
        
        # 2. Setup: Create a Recipe for Plank
        # Needs 1 Hammer (preserved) and 2 Wood (consumed)
        recipe = Recipe(id=1, game_token=self.game_token, item_id=12, rate_amount=1)
        db.session.add(recipe)
        db.session.flush()

        src1 = RecipeSource(game_token=self.game_token, recipe_id=1, item_id=10, q_required=1, preserve=True)
        src2 = RecipeSource(game_token=self.game_token, recipe_id=1, item_id=11, q_required=2, preserve=False)
        db.session.add_all([src1, src2])
        db.session.commit()

        # 3. Execution: Check production when inventory is empty
        possible, reason = can_perform_recipe(self.game_token, GENERAL_ID, recipe)
        self.assertFalse(possible)
        self.assertIn("Not enough Hammer", reason)

        # 4. Execution: Add only the tool
        db.session.add(Pile(game_token=self.game_token, item_id=10, owner_id=GENERAL_ID, quantity=1))
        db.session.commit()
        possible, reason = can_perform_recipe(self.game_token, GENERAL_ID, recipe)
        self.assertFalse(possible)
        self.assertIn("Not enough Wood", reason)

        # 5. Execution: Add resources
        db.session.add(Pile(game_token=self.game_token, item_id=11, owner_id=GENERAL_ID, quantity=10))
        db.session.commit()
        possible, reason = can_perform_recipe(self.game_token, GENERAL_ID, recipe)
        self.assertTrue(possible)

    def test_item_deletion_cascade(self):
        """Verify that deleting an item scrubs its recipes and inventory piles."""
        item = Item(id=50, game_token=self.game_token, name="DeleteMe", storage_type="universal")
        db.session.add(item)
        db.session.flush()
        
        # Add a pile and a recipe
        db.session.add(Pile(game_token=self.game_token, item_id=50, owner_id=GENERAL_ID, quantity=10))
        db.session.add(Recipe(id=50, game_token=self.game_token, item_id=50))
        db.session.commit()

        # Verify exists
        self.assertIsNotNone(Pile.query.filter_by(item_id=50, game_token=self.game_token).first())

        # Delete the item
        db.session.delete(item)
        db.session.commit()

        # Verify cascades wiped related data
        self.assertIsNone(Pile.query.filter_by(item_id=50, game_token=self.game_token).first())
        self.assertIsNone(Recipe.query.filter_by(item_id=50, game_token=self.game_token).first())

    def test_import_hydration(self):
        """Verify that the serialization service correctly hydrates a complex item JSON."""
        scenario_data = {
            "items": [
                {
                    "id": 200,
                    "name": "Magic Wand",
                    "storage_type": "universal",
                    "quantity": 5.0,
                    "recipes": [
                        {
                            "id": 99,
                            "rate_amount": 1,
                            "rate_duration": 10,
                            "sources": [{"item_id": 201, "q_required": 1}]
                        }
                    ]
                },
                {
                    "id": 201,
                    "name": "Stick",
                    "storage_type": "universal",
                    "quantity": 0
                }
            ]
        }

        import_from_dict(scenario_data, self.game_token)

        # 1. Check item was created
        wand = Item.query.get((200, self.game_token))
        self.assertEqual(wand.name, "Magic Wand")

        # 2. Check quantity was mapped to Pile Owner ID 1
        pile = Pile.query.filter_by(item_id=200, owner_id=GENERAL_ID).first()
        self.assertEqual(pile.quantity, 5.0)

        # 3. Check recipe was reconstructed
        recipe = Recipe.query.get((99, self.game_token))
        self.assertEqual(recipe.item_id, 200)
        self.assertEqual(recipe.sources[0].item_id, 201)

if __name__ == '__main__':
    unittest.main()
