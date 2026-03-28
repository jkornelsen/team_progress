from .testing_utils import BaseTestCase
from app.models import db, Item, Pile
from app.src.logic_piles import adjust_quantity, transfer_item, GENERAL_ID

class TestPileLogic(BaseTestCase):

    def setUp(self):
        super().setUp()
        # Create a dummy item for testing
        self.item = Item(id=10, game_token=self.game_token, name="Iron", storage_type="universal")
        db.session.add(self.item)
        db.session.commit()

    def test_adjust_quantity(self):
        # Add 50 to general storage
        adjust_quantity(10, GENERAL_ID, 50.0)
        
        pile = Pile.query.filter_by(item_id=10, owner_id=GENERAL_ID).first()
        self.assertEqual(pile.quantity, 50.0)

        # Subtract 10
        adjust_quantity(10, GENERAL_ID, -10.0)
        self.assertEqual(pile.quantity, 40.0)

    def test_cleanup_on_zero(self):
        adjust_quantity(10, GENERAL_ID, 10.0)
        adjust_quantity(10, GENERAL_ID, -10.0)
        
        # Record should be deleted from DB entirely
        pile = Pile.query.filter_by(item_id=10, owner_id=GENERAL_ID).first()
        self.assertIsNone(pile)
