"""
To run:
$env:PYTHONPATH="."
pytest -v -s --tb=line tests/test_numbers.py
pytest -v -s --tb=line tests/test_numbers.py::test_commas
"""
from flask import g
import pytest
from unittest.mock import patch, MagicMock
import locale
import re

from app import app
from src.utils import SUFFIXES, format_num

@pytest.fixture
def app_context():
    app.config['TESTING'] = True
    with app.app_context():
        g.game_token = 'test1234'
        yield

@pytest.fixture
def mock_g():
    """Fixture to mock Flask's g object."""
    mock_g_instance = MagicMock()
    mock_g_instance.game_data.overall.number_format = ''
    patch('src.utils.g', mock_g_instance).start()
    yield mock_g_instance

def test_basic_numbers(app_context, mock_g):
    ## Invalid
    assert format_num(None) == ''
    assert format_num('') == ''
    assert format_num('invalid') == ''
    assert format_num(0) == '0'
    assert format_num('0') == '0'
    ## Decimals
    assert format_num(100.0) == '100'
    ## Strings
    assert format_num('1') == '1'
    assert format_num('100') == '100'

def test_scientific_formats(app_context, mock_g):
    mock_g.game_data.overall.number_format = 'sci'
    assert format_num(1234567) == '1.23e6'
    assert format_num(-1234567) == '-1.23e6'
    mock_g.game_data.overall.number_format = 'abbr'
    assert format_num(1000) == '1.00k'
    assert format_num(1500000) == '1.50m'
    assert format_num(2500000000) == '2.50b'

def test_commas(app_context, mock_g):
    assert format_num(1000.00) == '1,000'
    assert format_num('10000') == '10,000'
    mock_g.game_data.overall.number_format = 'en_US'
    assert format_num(1234567.890) == '1,234,567.89'
    assert format_num(0.1) == '0.1'
    assert format_num(1000000000000) == '1,000,000,000,000'
    assert format_num(1234.5678) == '1,234.5678'
    assert format_num(1234) == '1,234'
    assert format_num('1234') == '1,234'
    assert format_num('12345.678') == '12,345.678'
    mock_g.game_data.overall.number_format = 'invalid_locale'
    assert format_num(1234) == '1234'
