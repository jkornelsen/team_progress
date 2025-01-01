import unittest
from unittest.mock import patch, MagicMock
from flask import g
from app import app
from src.utils import SUFFIXES, format_num

class TestFormatNumbers(unittest.TestCase):

    def setUp(self):
        """Set up app context and mock g."""
        app.config['TESTING'] = True
        self.app_context = app.app_context()
        self.app_context.push()
        g.game_token = 'test1234'
        
        # Mock g
        self.mock_g = MagicMock()
        self.mock_g.game_data.overall.number_format = ''
        patch('src.utils.g', self.mock_g).start()

    def tearDown(self):
        """Tear down the app context."""
        self.app_context.pop()

    def test_basic_numbers(self):
        """Test basic number formats."""
        # Invalid
        self.assertEqual(format_num(None), '')
        self.assertEqual(format_num(''), '')
        self.assertEqual(format_num('invalid'), '')
        self.assertEqual(format_num(0), '0')
        self.assertEqual(format_num('0'), '0')

        # Decimals
        self.assertEqual(format_num(100.0), '100')

        # Strings
        self.assertEqual(format_num('1'), '1')
        self.assertEqual(format_num('100'), '100')

    def test_scientific_formats(self):
        """Test scientific format."""
        self.mock_g.game_data.overall.number_format = 'sci'
        self.assertEqual(format_num(1234567), '1.23e6')
        self.assertEqual(format_num(-1234567), '-1.23e6')

        # Abbreviation format
        self.mock_g.game_data.overall.number_format = 'abbr'
        self.assertEqual(format_num(1000), '1.00k')
        self.assertEqual(format_num(1500000), '1.50m')
        self.assertEqual(format_num(2500000000), '2.50b')

    def test_commas(self):
        """Test formatting with commas."""
        self.assertEqual(format_num(1000.00), '1,000')
        self.assertEqual(format_num('10000'), '10,000')

        # Test with en_US locale
        self.mock_g.game_data.overall.number_format = 'en_US'
        self.assertEqual(format_num(1234567.890), '1,234,567.89')
        self.assertEqual(format_num(0.1), '0.1')
        self.assertEqual(format_num(1000000000000), '1,000,000,000,000')
        self.assertEqual(format_num(1234.5678), '1,234.5678')
        self.assertEqual(format_num(1234), '1,234')
        self.assertEqual(format_num('1234'), '1,234')
        self.assertEqual(format_num('12345.678'), '12,345.678')

        # Invalid locale
        self.mock_g.game_data.overall.number_format = 'invalid_locale'
        self.assertEqual(format_num(1234), '1234')

if __name__ == '__main__':
    unittest.main()
