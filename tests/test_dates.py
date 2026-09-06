import unittest
from utils.dates import parse_date, parse_time
from datetime import date

class TestDates(unittest.TestCase):

    def test_parse_date(self):
        self.assertEqual(parse_date("31 Aug 2026"), date(2026, 8, 31))
        self.assertEqual(parse_date("06 Sep 2026"), date(2026, 9, 6))
        self.assertEqual(parse_date("04Sep2026"), date(2026, 9, 4))
        self.assertEqual(parse_date("31 Aug", fallback_year=2026), date(2026, 8, 31))

    def test_parse_time(self):
        self.assertEqual(parse_time("08:25 PM"), "08:25 PM")
        self.assertEqual(parse_time("10:30 AM"), "10:30 AM")
        self.assertEqual(parse_time("10:02AM"), "10:02 AM")
        self.assertEqual(parse_time("14:30"), "14:30")

if __name__ == '__main__':
    unittest.main()
