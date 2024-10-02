"""
To run:
$env:PYTHONPATH="."
pytest -s
pytest -v -s tests/test_item.py
pytest -W error::DeprecationWarning tests/test_item.py -v
"""
from flask import g
from psycopg2.extras import RealDictCursor
from unittest.mock import patch, MagicMock
import pytest

from app import app
from src.item import Item

@pytest.fixture
def app_context():
    app.config['TESTING'] = True
    with app.app_context():
        g.game_token = 'test1234'
        yield

@pytest.fixture
def mock_g():
    mock_g_instance = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {'id': 1}
    mock_g_instance.db.cursor.return_value.__enter__.return_value = mock_cursor
    patch('src.db_serializable.g', mock_g_instance).start()
    yield mock_g_instance, mock_cursor

def test_create_item(app_context, mock_g):
    mock_g, mock_cursor = mock_g
    for new_id in [0, "", None, "new", "0", 0.0, float("0.0")]:
        item = Item(new_id)
        assert item.id == 0
        assert isinstance(item.id, int)
    item = Item()
    item.to_db()
    mock_g.db.cursor.assert_called_with(cursor_factory=RealDictCursor)
    call_args = [
        call[0] for call in mock_cursor.execute.call_args_list]
    #print(call_args)
    return
    expected_sql = [
        ('INSERT INTO progress', 0),
        ('INSERT INTO items', ''),
        ('DELETE FROM item_attribs', 1),
        ('DELETE FROM recipes', 1)
        ]
    assert len(call_args) == len(expected_sql)
    for i, (sql_part, expected_first_value) in enumerate(expected_sql):
        arg_query, arg_values = call_args[i]
        assert sql_part in arg_query
        assert arg_values[0] == expected_first_value
