import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from photo_backer_upper.naming import choose

DENVER = ZoneInfo("America/Denver")
F = "%Y-%m-%d_%H-%M-%S"


class ChooseTests(unittest.TestCase):
    def test_utc_date_becomes_local(self):
        date = datetime(2013, 10, 22, 1, 23, 56, tzinfo=timezone.utc)
        original = datetime(2013, 10, 21, 19, 23, 56, 854000, tzinfo=DENVER)
        self.assertEqual(choose(date, original).strftime(F), "2013-10-21_19-23-56")

    def test_utc_with_one_second_difference_still_fixed(self):
        date = datetime(2015, 3, 9, 0, 39, 21, tzinfo=timezone.utc)
        original = datetime(2015, 3, 8, 18, 39, 22, tzinfo=DENVER)
        self.assertEqual(choose(date, original).strftime(F), "2015-03-08_18-39-22")

    def test_rounding_keeps_dates_second(self):
        date = datetime(2015, 2, 21, 19, 18, 21, 890, tzinfo=ZoneInfo("Etc/GMT+7"))
        original = datetime(2015, 2, 21, 19, 18, 20, 999000, tzinfo=DENVER)
        self.assertEqual(choose(date, original).strftime(F), "2015-02-21_19-18-21")

    def test_same_offset_one_second_apart_keeps_date(self):
        date = datetime(2017, 12, 10, 14, 50, 10, tzinfo=DENVER)
        original = date + timedelta(seconds=1)
        self.assertEqual(choose(date, original).strftime(F), "2017-12-10_14-50-10")

    def test_bogus_original_falls_back_to_date(self):
        date = datetime(2013, 10, 25, 2, 37, 24, tzinfo=timezone.utc)
        original = datetime(2026, 6, 24, 22, 52, 55, tzinfo=DENVER)
        self.assertEqual(choose(date, original).strftime(F), "2013-10-25_02-37-24")

    def test_missing_original(self):
        date = datetime(2020, 1, 1, 5, 0, 0, tzinfo=DENVER)
        self.assertEqual(choose(date, None), date)


if __name__ == "__main__":
    unittest.main()
