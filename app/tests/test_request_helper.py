import unittest
from flask import request
from .testing_utils import BaseTestCase
from app.utils import RequestHelper

class TestRequestHelper(BaseTestCase):
    
    def test_get_int_and_float(self):
        # We use the app.test_request_context to simulate a form submission
        with self.app.test_request_context('/?arg1=1&arg2=1.5&arg3=10k&arg4='):
            req = RequestHelper()
            
            # 1. Standard Integer
            self.assertEqual(req.get_int('arg1'), 1)
            
            # 2. Float with decimal
            self.assertEqual(req.get_float('arg2'), 1.5)
            
            # 3. Abbreviated format (The utility now handles this automatically!)
            self.assertEqual(req.get_int('arg3'), 10000)
            
            # 4. Empty/Missing with Default
            self.assertEqual(req.get_int('arg4', 5), 5)
            self.assertEqual(req.get_int('missing', 99), 99)

    def test_get_bool(self):
        with self.app.test_request_context('/?a=true&b=on&c=1&d=checked&e=false'):
            req = RequestHelper()
            self.assertTrue(req.get_bool('a'))
            self.assertTrue(req.get_bool('b'))
            self.assertTrue(req.get_bool('c'))
            self.assertTrue(req.get_bool('d'))
            self.assertFalse(req.get_bool('e'))

if __name__ == '__main__':
    unittest.main()