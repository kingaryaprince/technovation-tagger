from datetime import date
import unittest
from alumni_tagger.collection_schedule import due


class ScheduleTests(unittest.TestCase):
    def test_exact_fourteen_day_intervals_across_months(self):
        for d in ['2026-11-16', '2026-11-30', '2026-12-14', '2026-12-28', '2027-01-11']:
            self.assertTrue(due(date.fromisoformat(d)), d)
        for d in ['2026-11-02', '2026-11-17', '2026-11-23', '2026-12-01', '2027-01-04']:
            self.assertFalse(due(date.fromisoformat(d)), d)
