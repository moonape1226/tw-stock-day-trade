#!/usr/bin/env python3
import unittest

from update_watchlist import select_stocks, build_new_config


def cand(code, bucket, z=100.0):
    return {"code": code, "name": f"N{code}", "bucket": bucket, "z": z}


class TestSelectStocks(unittest.TestCase):
    def test_bucket_a_before_b_and_capped_at_target(self):
        cands = [cand(f"A{i}", "A") for i in range(4)] + [cand(f"B{i}", "B") for i in range(4)]
        out = select_stocks(cands, target=5)
        self.assertEqual([s["symbol"] for s in out], ["A0", "A1", "A2", "A3", "B0"])

    def test_excludes_bucket_c(self):
        cands = [cand("A0", "A"), cand("C0", "C"), cand("B0", "B")]
        out = select_stocks(cands, target=5)
        self.assertEqual([s["symbol"] for s in out], ["A0", "B0"])

    def test_returns_fewer_when_ab_below_target(self):
        cands = [cand("A0", "A"), cand("B0", "B"), cand("C0", "C")]
        out = select_stocks(cands, target=5)
        self.assertEqual(len(out), 2)

    def test_maps_code_and_name_to_symbol(self):
        out = select_stocks([{"code": "2330", "name": "台積電", "bucket": "A", "z": 480.0}], target=5)
        self.assertEqual(out, [{"symbol": "2330", "name": "台積電"}])

    def test_excludes_price_above_ceiling(self):
        cands = [cand("A0", "A", z=975.0), cand("A1", "A", z=480.0), cand("B0", "B", z=600.0)]
        out = select_stocks(cands, target=5, price_max=500)
        self.assertEqual([s["symbol"] for s in out], ["A1"])

    def test_keeps_price_equal_to_ceiling(self):
        out = select_stocks([cand("A0", "A", z=500.0)], target=5, price_max=500)
        self.assertEqual([s["symbol"] for s in out], ["A0"])

    def test_excludes_bad_price_zero(self):
        out = select_stocks([cand("A0", "A", z=0.0)], target=5, price_max=500)
        self.assertEqual(out, [])


class TestBuildNewConfig(unittest.TestCase):
    def test_replaces_stocks_preserves_index_and_strategy(self):
        cfg = {
            "refresh_seconds": 30,
            "stocks": [{"symbol": "1111", "name": "old"}],
            "index": {"symbol": "001", "name": "加權指數"},
            "strategy": {"breakout_volume_ratio": 1.5},
        }
        new_stocks = [{"symbol": "2330", "name": "台積電"}]
        out = build_new_config(cfg, new_stocks)
        self.assertEqual(out["stocks"], new_stocks)
        self.assertEqual(out["index"], cfg["index"])
        self.assertEqual(out["strategy"], cfg["strategy"])
        self.assertEqual(out["refresh_seconds"], 30)

    def test_does_not_mutate_input_config(self):
        cfg = {"stocks": [{"symbol": "1111", "name": "old"}], "index": {}, "strategy": {}}
        build_new_config(cfg, [{"symbol": "2330", "name": "台積電"}])
        self.assertEqual(cfg["stocks"], [{"symbol": "1111", "name": "old"}])


if __name__ == "__main__":
    unittest.main()
