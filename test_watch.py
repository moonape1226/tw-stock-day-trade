#!/usr/bin/env python3
"""watch.py 純邏輯單元測試。執行: python3 test_watch.py"""
import unittest

from watch import stock_vwap_dir, intraday_rvol, ext_threshold


class StockVwapDir(unittest.TestCase):
    def test_above_vwap_rising_or_flat_is_long(self):
        self.assertEqual(stock_vwap_dir(101, 100, 100), 1)   # 上漲
        self.assertEqual(stock_vwap_dir(101, 100, 101), 1)   # 持平

    def test_above_vwap_falling_is_neutral(self):
        # 站上 VWAP 但較前價下跌 → 回檔中不視為多方
        self.assertEqual(stock_vwap_dir(101, 100, 102), 0)

    def test_below_vwap_falling_or_flat_is_short(self):
        self.assertEqual(stock_vwap_dir(99, 100, 100), -1)   # 下跌
        self.assertEqual(stock_vwap_dir(99, 100, 99), -1)    # 持平

    def test_below_vwap_rebounding_is_neutral(self):
        # 跌破 VWAP 但較前價反彈 → 不視為空方
        self.assertEqual(stock_vwap_dir(99, 100, 98), 0)

    def test_no_prev_price_returns_sign_of_price_minus_vwap(self):
        self.assertEqual(stock_vwap_dir(101, 100, None), 1)
        self.assertEqual(stock_vwap_dir(99, 100, None), -1)
        self.assertEqual(stock_vwap_dir(100, 100, None), 0)

    def test_non_positive_prev_price_returns_sign_of_price_minus_vwap(self):
        self.assertEqual(stock_vwap_dir(101, 100, 0), 1)
        self.assertEqual(stock_vwap_dir(99, 100, -5), -1)
        self.assertEqual(stock_vwap_dir(100, 100, 0), 0)

    def test_price_equals_vwap_is_neutral(self):
        self.assertEqual(stock_vwap_dir(100, 100, 99), 0)
        self.assertEqual(stock_vwap_dir(100, 100, 101), 0)


class IntradayRvol(unittest.TestCase):
    # FinMind total_volume 與 MIS v 同單位=張(經 2408 實測 108299==108299 確認),
    # avg20_lots 亦為張,故同口徑相除、不再 /1000。
    def test_basic_ratio(self):
        # cum 750 張 / (1000 張 × 0.5 frac) = 1.5
        self.assertAlmostEqual(intraday_rvol(750, 1000, 0.5), 1.5)

    def test_on_pace_full_day_is_one(self):
        # cum 1000 張 / (1000 張 × 1.0 frac) = 1.0
        self.assertAlmostEqual(intraday_rvol(1000, 1000, 1.0), 1.0)

    def test_real_2408_below_average_day(self):
        # 2408 收盤(frac=1.0): 108299 張 / 136707.5 張 ≈ 0.792
        self.assertAlmostEqual(intraday_rvol(108299, 136707.5, 1.0), 0.7922, places=3)

    def test_non_positive_avg20_returns_zero(self):
        self.assertEqual(intraday_rvol(750, 0, 0.5), 0.0)
        self.assertEqual(intraday_rvol(750, -10, 0.5), 0.0)

    def test_non_positive_frac_returns_zero(self):
        self.assertEqual(intraday_rvol(750, 1000, 0), 0.0)
        self.assertEqual(intraday_rvol(750, 1000, -0.1), 0.0)


class ExtThreshold(unittest.TestCase):
    def test_no_atr_uses_fixed_threshold(self):
        self.assertEqual(ext_threshold(None), 2.0)

    def test_scales_with_atr(self):
        self.assertAlmostEqual(ext_threshold(5.0), 3.0)   # 0.6 × 5.0 = 3.0
        self.assertAlmostEqual(ext_threshold(2.0), 1.2)   # 0.6 × 2.0 = 1.2

    def test_floor(self):
        self.assertAlmostEqual(ext_threshold(1.0), 1.0)   # 0.6 被 floor 抬到 1.0

    def test_ceil(self):
        self.assertAlmostEqual(ext_threshold(10.0), 4.0)  # 0.6 × 10.0 = 6.0 被 ceil 壓到 4.0


if __name__ == "__main__":
    unittest.main()
