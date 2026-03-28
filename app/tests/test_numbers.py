import unittest
from .testing_utils import BaseTestCase
from app.utils import format_num, unformat_num

class TestNumberUtilities(BaseTestCase):

    def test_basic_formatting(self):
        """Test standard numeric formatting and edge cases."""
        # Standard integers (less than 1000)
        self.assertEqual(format_num(100), "100")
        self.assertEqual(format_num(0), "0")
        self.assertEqual(format_num(-5), "-5")

        # Edge cases
        self.assertEqual(format_num(None), "")
        self.assertEqual(format_num(""), "")
        self.assertEqual(format_num("not_a_number"), "not_a_number")

    def test_scientific_format(self):
        """Test the 'sci' format (1.23e6)."""
        fmt = "sci"
        self.assertEqual(format_num(1000, fmt), "1.00e3")
        self.assertEqual(format_num(1234567, fmt), "1.23e6")
        self.assertEqual(format_num(0.000123, fmt), "1.23e-4")
        self.assertEqual(format_num(-1234, fmt), "-1.23e3")

    def test_abbreviated_format(self):
        """Test the 'abbr' format (1.23k, 1.50m)."""
        fmt = "abbr"
        self.assertEqual(format_num(999, fmt), "999")
        self.assertEqual(format_num(1000, fmt), "1.00k")
        self.assertEqual(format_num(1500, fmt), "1.50k")
        self.assertEqual(format_num(1000000, fmt), "1.00m")
        self.assertEqual(format_num(2500000000, fmt), "2.50b")
        self.assertEqual(format_num(-1500000, fmt), "-1.50m")

    def test_locale_formatting(self):
        """Test standard locale-based formatting (commas/dots)."""
        # Default en_US
        self.assertEqual(format_num(1234.56, "en_US"), "1,234.56")
        self.assertEqual(format_num(1000000, "en_US"), "1,000,000")
        
        # Note: 'de_DE' behavior depends on system locale availability, 
        # but the utility falls back to 'C' (1234.56) if missing.

    def test_unformatting(self):
        """Test parsing strings back into floats (Inverse operations)."""
        # Standard
        self.assertEqual(unformat_num("100"), 100.0)
        self.assertEqual(unformat_num("1,234.56"), 1234.56)
        
        # Scientific
        self.assertEqual(unformat_num("1.23e6"), 1230000.0)
        self.assertEqual(unformat_num("1.23e-4"), 0.000123)
        
        # Abbreviated
        self.assertEqual(unformat_num("1k"), 1000.0)
        self.assertEqual(unformat_num("1.5m"), 1500000.0)
        self.assertEqual(unformat_num("2.5b"), 2500000000.0)
        self.assertEqual(unformat_num("-1.5m"), -1500000.0)

        # Garbage in
        self.assertEqual(unformat_num("abc"), 0.0)
        self.assertEqual(unformat_num(""), 0.0)
        self.assertEqual(unformat_num(None), 0.0)

    def test_round_trip(self):
        """Verify that formatting and then unformatting returns the same value."""
        original_val = 1500000.0
        
        # Test Abbreviated round-trip
        formatted = format_num(original_val, "abbr") # "1.50m"
        result = unformat_num(formatted)
        self.assertEqual(original_val, result)
        
        # Test Scientific round-trip
        formatted_sci = format_num(original_val, "sci") # "1.50e6"
        result_sci = unformat_num(formatted_sci)
        self.assertEqual(original_val, result_sci)

if __name__ == '__main__':
    unittest.main()