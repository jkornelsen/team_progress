"""
To run:
python -m unittest tests/test_item_integrated.py
"""
import unittest
from unittest.mock import patch
from flask import g
import logging

from app import app
from src.item import Item
from tests.testing_utils import setup_with_db

class Test1(unittest.TestCase):
    def setUp(self):
        """Set up app context and mock g."""
        self.app_context, _ = setup_with_db()

    def tearDown(self):
        """Tear down the app context."""
        self.app_context.pop()

    def test_create_item(self):
        """Test creating an item."""
        item1_data = {
            'name': "item1",
            'description': "item1desc",
        }
        item = Item.from_data(item1_data)
        item.to_db()
        item1_id = item.id
        self.assertNotIn(item1_id, g.game_data.items)
        Item.load_complete_objects()
        self.assertIn(item1_id, g.game_data.items)
        item = g.game_data.items[item1_id]
        self.assertEqual(item.name, item1_data['name'])
        self.assertEqual(item.description, item1_data['description'])

class Test2_RecipesOfSources(unittest.TestCase):
    def setUp(self):
        self.app_context, _ = setup_with_db()
        data = {
            "attribs": [],
            "characters": [],
            "events": [],
            "items": [
                {
                    "attribs": [],
                    "description": "Expected to lose one of the recipes if the problematic behavior persists.",
                    "id": 1,
                    "masked": False,
                    "name": "item1",
                    "progress": {},
                    "q_limit": 0.0,
                    "quantity": 0.0,
                    "recipes": [
                        {
                            "attrib_reqs": [],
                            "byproducts": [],
                            "id": 1,
                            "instant": False,
                            "rate_amount": 1.0,
                            "rate_duration": 3.0,
                            "sources": [
                                {
                                    "item_id": 2,
                                    "preserve": True,
                                    "q_required": 1.0
                                }
                            ]
                        },
                        {
                            "attrib_reqs": [],
                            "byproducts": [],
                            "id": 2,
                            "instant": False,
                            "rate_amount": 1.0,
                            "rate_duration": 3.0,
                            "sources": [
                                {
                                    "item_id": 3,
                                    "preserve": True,
                                    "q_required": 1.0
                                },
                                {
                                    "item_id": 4,
                                    "preserve": True,
                                    "q_required": 1.0
                                }
                            ]
                        }
                    ],
                    "storage_type": "universal",
                    "toplevel": True
                },
                {
                    "attribs": [],
                    "description": "",
                    "id": 2,
                    "masked": False,
                    "name": "item2",
                    "progress": {},
                    "q_limit": 0.0,
                    "quantity": 100.0,
                    "recipes": [],
                    "storage_type": "universal",
                    "toplevel": False
                },
                {
                    "attribs": [],
                    "description": "",
                    "id": 3,
                    "masked": False,
                    "name": "item3",
                    "progress": {},
                    "q_limit": 0.0,
                    "quantity": 100.0,
                    "recipes": [],
                    "storage_type": "universal",
                    "toplevel": False
                },
                {
                    "attribs": [],
                    "description": "Playing this item is expected to cause item1 to lose a recipe.",
                    "id": 4,
                    "masked": False,
                    "name": "item4",
                    "progress": {},
                    "q_limit": 0.0,
                    "quantity": 0,
                    "recipes": [
                        {
                            "attrib_reqs": [],
                            "byproducts": [],
                            "id": 3,
                            "instant": False,
                            "rate_amount": 1.0,
                            "rate_duration": 3.0,
                            "sources": [
                                {
                                    "item_id": 1,
                                    "preserve": False,
                                    "q_required": 1.0
                                }
                            ]
                        }
                    ],
                    "storage_type": "local",
                    "toplevel": False
                }
            ],
            "locations": [
                {
                    "description": "",
                    "destinations": [],
                    "dimensions": [0,0],
                    "excluded": [0,0,0,0],
                    "id": 1,
                    "item_refs": [],
                    "items": [
                        {
                            "item_id": 4,
                            "position": [0,0],
                            "quantity": 0.0
                        }
                    ],
                    "masked": False,
                    "name": "loc1",
                    "toplevel": True
                }
            ],
            "overall": {
                "description": "",
                "multiplayer": False,
                "number_format": "en_US",
                "progress_type": "?",
                "slots": [],
                "title": "Generic Adventure",
                "win_reqs": []
            }
        }
        g.game_data.from_json(data)
        g.game_data.to_db()

    def tearDown(self):
        """Tear down the app context."""
        self.app_context.pop()

    def test2(self):
        """Test the recipes of sources for items."""
        item1 = Item.data_for_play(1)
        self.assertEqual(len(item1.recipes), 2)
        item4 = Item.data_for_play(4)
        self.assertEqual(len(item4.recipes), 1)
        item1 = Item.data_for_play(1)
        self.assertEqual(len(item1.recipes), 2)

if __name__ == '__main__':
    unittest.main()
