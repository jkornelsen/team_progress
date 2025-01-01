import unittest
from unittest.mock import patch, MagicMock
from flask import g
from app import app
from src.utils import RequestHelper

class TestRequestHelper(unittest.TestCase):
    
    def setUp(self):
        """Set up app context and mock request."""
        app.config['TESTING'] = True
        self.app_context = app.app_context()
        self.app_context.push()
        g.game_token = 'test1234'
        
        # Mock request
        self.mock_request = MagicMock()
        self.mock_request.form = {}
        patch('src.utils.request', self.mock_request).start()
        
    def tearDown(self):
        """Tear down the app context."""
        self.app_context.pop()
        
    def test_get_int_and_float(self):
        req = RequestHelper('form')
        
        # Test integer retrieval
        self.mock_request.form['arg1'] = '1'
        self.assertEqual(req.get_int('arg1', 2), 1)
        
        self.mock_request.form['arg1'] = '1.1'
        self.assertEqual(req.get_int('arg1', 2), 1)
        
        # Test float retrieval
        self.assertEqual(req.get_float('arg1', 2.0), 1.1)
        
        # Test zero value retrieval
        self.mock_request.form['arg1'] = '0'
        self.assertEqual(req.get_int('arg1', 2), 0)
        self.assertEqual(req.get_float('arg1', 2.0), 0.0)
        
        self.mock_request.form['arg1'] = '0.0'
        self.assertEqual(req.get_int('arg1', 2), 0)
        self.assertEqual(req.get_float('arg1', 2.0), 0.0)
        
        # Test default value when no argument
        self.mock_request.form['arg1'] = ''
        self.assertEqual(req.get_int('arg1', 2), 2)
        self.assertEqual(req.get_float('arg1', 2.1), 2.1)

if __name__ == '__main__':
    unittest.main()
