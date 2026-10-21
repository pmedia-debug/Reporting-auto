import sys
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from engine import period_end, period_after, reporting_dates

class CalendarTests(unittest.TestCase):
    def test_cutoffs(self):
        self.assertEqual(reporting_dates('Asia/Kolkata',3,'2026-10-16'),('2026-10-01','2026-10-07'))
        self.assertEqual(reporting_dates('Asia/Kolkata',3,'2026-10-17'),('2026-10-08','2026-10-14'))
    def test_month_end(self):
        self.assertEqual(period_end(date(2026,10,29)),date(2026,10,31))
        self.assertEqual(period_after(date(2026,10,29)),date(2026,11,1))
        self.assertEqual(period_end(date(2027,2,22)),date(2027,2,28))
        self.assertEqual(period_after(date(2027,2,22)),date(2027,3,1))
if __name__ == '__main__': unittest.main()
