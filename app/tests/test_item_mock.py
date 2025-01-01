import unittest
from unittest.mock import patch, MagicMock, call
from flask import g
from psycopg2.extras import RealDictCursor

from app import app
from src.item import Item

class TestItem(unittest.TestCase):

    def setUp(self):
        """Set up app context and mock g."""
        app.config['TESTING'] = True
        self.app_context = app.app_context()
        self.app_context.push()
        g.game_token = 'test1234'

        # Mock g
        self.mock_g = MagicMock()
        self.mock_cursor = MagicMock()
        self.mock_cursor.fetchone.return_value = {'id': 1}
        self.mock_g.db.cursor.return_value.__enter__.return_value = self.mock_cursor
        patch('src.db_serializable.g', self.mock_g).start()

    def tearDown(self):
        """Tear down the app context."""
        self.app_context.pop()

    def test_create_item(self):
        """Test creating an item."""
        for new_id in [0, "", None, "new", "0", 0.0, float("0.0")]:
            item = Item(new_id)
            self.assertEqual(item.id, 0)
            self.assertIsInstance(item.id, int)

        # Test to_db method
        item = Item()
        item.to_db()

        # Check if the cursor was created with the correct factory
        self.mock_g.db.cursor.assert_called_with(cursor_factory=RealDictCursor)

        # Check the queries executed by the cursor
        call_args = [
            call[0] for call in self.mock_cursor.execute.call_args_list]
        
        expected_sql = [
            ('INSERT INTO progress', 0),
            ('INSERT INTO items', ''),
            ('DELETE FROM item_attribs', 1),
            ('DELETE FROM recipes', 1)
        ]

        # Assert the number of calls match
        self.assertEqual(len(call_args), len(expected_sql))

        # Assert the queries contain expected values
        for i, (sql_part, expected_first_value) in enumerate(expected_sql):
            arg_query, arg_values = call_args[i]
            self.assertIn(sql_part, arg_query)
            self.assertEqual(arg_values[0], expected_first_value)

if __name__ == '__main__':
    unittest.main()
