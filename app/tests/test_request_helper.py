"""
To run:
$env:PYTHONPATH="."
pytest -v -s --tb=line tests/test_request_helper.py
"""
from flask import g
import pytest
from unittest.mock import patch, MagicMock
import locale
import re

from app import app
from src.utils import RequestHelper

@pytest.fixture
def app_context():
    app.config['TESTING'] = True
    with app.app_context():
        g.game_token = 'test1234'
        yield

@pytest.fixture
def mock_request():
    """Fixture to mock Flask's request object."""
    mock_request_instance = MagicMock()
    mock_request_instance.form = {}
    patch('src.utils.request', mock_request_instance).start()
    yield mock_request_instance

def test1(app_context, mock_request):
    req = RequestHelper('form')
    mock_request.form['arg1'] = '1'
    assert req.get_int('arg1', 2) == 1
    mock_request.form['arg1'] = '1.1'
    assert req.get_int('arg1', 2) == 1
    assert req.get_float('arg1', 2.0) == 1.1
    mock_request.form['arg1'] = '0'
    assert req.get_int('arg1', 2) == 0
    assert req.get_float('arg1', 2.0) == 0.0
    mock_request.form['arg1'] = '0.0'
    assert req.get_int('arg1', 2) == 0
    assert req.get_float('arg1', 2.0) == 0.0
    mock_request.form['arg1'] = ''
    assert req.get_int('arg1', 2) == 2
    assert req.get_float('arg1', 2.1) == 2.1
