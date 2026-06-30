#!/usr/bin/env python3
"""screen_v2 單元測試。"""
import unittest
from datetime import datetime, timezone, timedelta

from screen_v2 import expected_vol_fraction, INTRADAY_VOL_CURVE

TZ = timezone(timedelta(hours=8))


def at(h, m):
    return datetime(2026, 6, 25, h, m, tzinfo=TZ)


class TestExpectedVolFraction(unittest.TestCase):
    def test_known_points(self):
        self.assertEqual(expected_vol_fraction(at(9, 45)), 0.403)
        self.assertEqual(expected_vol_fraction(at(9, 30)), 0.331)
        self.assertEqual(expected_vol_fraction(at(12, 0)), 0.755)
        self.assertEqual(expected_vol_fraction(at(14, 0)), 1.0)
        self.assertEqual(expected_vol_fraction(at(8, 0)), 0.02)

    def test_monotonic_non_decreasing(self):
        prev = -1.0
        for hm in range(0, 24 * 60):
            f = expected_vol_fraction(at(hm // 60, hm % 60))
            self.assertGreaterEqual(f, prev)
            prev = f


if __name__ == "__main__":
    unittest.main()
