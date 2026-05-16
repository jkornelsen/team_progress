"""
Run from project root in venv:
python -m unittest app.tests.test_progress_crash
"""
import unittest
from datetime import datetime, timedelta
from flask import g, session
from .testing_utils import BaseTestCase
from app.models import db, Entity, Item, Recipe, RecipeSource, Progress, GENERAL_ID, StorageType
from app.src.logic_progress import tick_all_active

class TestProgressCrash(BaseTestCase):

    def setUp(self):
        # We MUST push the request context BEFORE calling super().setUp()
        # because BaseTestCase calls init_game_session, which needs 'g'.
        self._req_ctx = create_app().test_request_context()
        self._req_ctx.push()
        
        # Now call the parent setup
        super().setUp()
        
        g.game_token = self.game_token
        session['username'] = 'test_user'

        # 1. Setup Entities (Host IDs)
        # Create a character or dummy entity to act as the second host
        other_host = Entity(
            id=99, 
            game_token=self.game_token, 
            name="Host 99", 
            entity_type='entity'
        )
        db.session.add(other_host)

        # 2. Setup Items
        # Wood (Ingredient)
        self.item_a = Item(
            id=10, game_token=self.game_token, name="Wood", 
            storage_type=StorageType.UNIVERSAL, entity_type='item'
        )
        # Plank (Masked Product - Gaining this triggers logic_discovery.py -> commit)
        self.item_b = Item(
            id=11, game_token=self.game_token, name="Plank", 
            storage_type=StorageType.UNIVERSAL, masked=True, entity_type='item'
        )
        
        db.session.add_all([self.item_a, self.item_b])
        db.session.flush()
        
        # 3. Setup Recipe: 1 Wood -> 1 Plank (3s duration)
        self.recipe = Recipe(
            id=50, game_token=self.game_token, product_id=11, 
            rate_amount=1.0, rate_duration=3, instant=False
        )
        db.session.add(self.recipe)
        db.session.flush()
        
        src = RecipeSource(
            game_token=self.game_token, recipe_id=50, item_id=10, q_required=1.0
        )
        db.session.add(src)
        
        # 4. Add wood to storage so production can happen
        from app.src.logic_piles import adjust_quantity
        adjust_quantity(10, GENERAL_ID, 100.0)
        db.session.commit()

    def tearDown(self):
        # Clean up the context
        if self._req_ctx:
            self._req_ctx.pop()
        super().tearDown()

    def test_tick_crash_on_unmasking(self):
        """
        Forces ObjectDeletedError.
        p1 unmasks Plank -> calls db.session.commit() -> expires p2.
        The loop in tick_all_active then tries to access p2 and crashes.
        """
        # Started 10 seconds ago = 3 batches ready
        start_time = datetime.now() - timedelta(seconds=10)
        
        p1 = Progress(
            game_token=self.game_token, recipe_id=50, product_id=11,
            owner_id=GENERAL_ID, host_id=GENERAL_ID,
            start_time=start_time, batches_processed=0
        )
        p2 = Progress(
            game_token=self.game_token, recipe_id=50, product_id=11,
            owner_id=GENERAL_ID, host_id=99, 
            start_time=start_time, batches_processed=0
        )
        db.session.add_all([p1, p2])
        db.session.commit()
        
        print("\n--- Starting tick_all_active ---")
        
        # If the bug exists, this call will raise sqlalchemy.orm.exc.ObjectDeletedError
        tick_all_active()
        
        # Verify that work was actually done (if we didn't crash)
        # We need to refresh the objects because the session was committed/expired
        db.session.refresh(p1)
        db.session.refresh(p2)
        
        self.assertGreater(p1.batches_processed, 0, "P1 should have processed batches")
        self.assertGreater(p2.batches_processed, 0, "P2 should have processed batches")
        print("--- Finished successfully (Bug NOT reproduced) ---")

def create_app():
    # Helper to get app inside setUp
    from app import create_app as _create_app
    return create_app()

if __name__ == '__main__':
    unittest.main()
