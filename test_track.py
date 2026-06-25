#!/usr/bin/env python3
"""track.py 純邏輯單元測試。執行: python3 test_track.py"""
import unittest
from datetime import datetime, timezone, timedelta

from track import check_exit, realized_r, parse_new_entries, build_closed_row, CLOSED_HEADER

TZ = timezone(timedelta(hours=8))


def at(h, m):
    return datetime(2026, 6, 25, h, m, 0, tzinfo=TZ)


class CheckExit(unittest.TestCase):
    # 全倉做多 1.5R: entry=110 sl=100 tp=125
    def test_sl_hit_when_price_at_or_below_sl(self):
        self.assertEqual(check_exit(100, 125, 100, at(10, 0)), ("SL", 100))
        self.assertEqual(check_exit(100, 125, 98.5, at(10, 0)), ("SL", 100))

    def test_tp_hit_when_price_at_or_above_tp(self):
        self.assertEqual(check_exit(100, 125, 125, at(10, 0)), ("TP", 125))
        self.assertEqual(check_exit(100, 125, 130, at(10, 0)), ("TP", 125))

    def test_no_exit_between_sl_and_tp_before_cutoff(self):
        self.assertIsNone(check_exit(100, 125, 108, at(10, 0)))

    def test_time_cutoff_closes_at_current_price(self):
        self.assertEqual(check_exit(100, 125, 108, at(13, 15)), ("CLOSE", 108))
        self.assertEqual(check_exit(100, 125, 108, at(13, 25)), ("CLOSE", 108))

    def test_sl_takes_priority_over_cutoff(self):
        # 過 13:15 但價格已破 SL → 記 SL 不記 CLOSE
        self.assertEqual(check_exit(100, 125, 99, at(13, 20)), ("SL", 100))

    def test_tp_takes_priority_over_cutoff(self):
        self.assertEqual(check_exit(100, 125, 126, at(13, 20)), ("TP", 125))


class RealizedR(unittest.TestCase):
    def test_tp_exit_gross_r_is_1_5(self):
        r = realized_r(110, 125, 100, 0.45)
        self.assertAlmostEqual(r["gross_r"], 1.5)

    def test_sl_exit_gross_r_is_minus_1(self):
        r = realized_r(110, 100, 100, 0.45)
        self.assertAlmostEqual(r["gross_r"], -1.0)

    def test_net_r_subtracts_round_trip_cost(self):
        # cost 0.45% of entry 110 = 0.495 元; risk=10 → net_r 比 gross 少 0.0495
        r = realized_r(110, 125, 100, 0.45)
        self.assertAlmostEqual(r["net_r"], 1.5 - 0.0495)

    def test_net_pnl_pct_subtracts_cost(self):
        r = realized_r(110, 125, 100, 0.45)
        self.assertAlmostEqual(r["net_pnl_pct"], (125 - 110) / 110 * 100 - 0.45)


class ParseNewEntries(unittest.TestCase):
    def _row(self, time, sym, entry="110", sl="100", tp="125", setup="BRK"):
        return {"time": time, "symbol": sym, "setup": setup,
                "entry": entry, "sl": sl, "tp": tp}

    def test_keeps_only_today_rows(self):
        rows = [self._row("2026-06-24 10:00:00", "2330"),
                self._row("2026-06-25 09:40:00", "2317")]
        out = parse_new_entries(rows, "2026-06-25", set())
        self.assertEqual([p["symbol"] for p in out], ["2317"])

    def test_excludes_already_seen_symbols(self):
        rows = [self._row("2026-06-25 09:40:00", "2317"),
                self._row("2026-06-25 09:41:00", "2454")]
        out = parse_new_entries(rows, "2026-06-25", {"2317"})
        self.assertEqual([p["symbol"] for p in out], ["2454"])

    def test_parses_prices_to_float(self):
        rows = [self._row("2026-06-25 09:40:00", "2317", entry="55.5", sl="54.0", tp="60.0")]
        p = parse_new_entries(rows, "2026-06-25", set())[0]
        self.assertEqual((p["entry"], p["sl"], p["tp"]), (55.5, 54.0, 60.0))
        self.assertEqual(p["setup"], "BRK")
        self.assertEqual(p["entry_ts"], "2026-06-25 09:40:00")

    def test_dedups_same_symbol_keeps_first(self):
        rows = [self._row("2026-06-25 09:40:00", "2317", entry="55"),
                self._row("2026-06-25 11:00:00", "2317", entry="58")]
        out = parse_new_entries(rows, "2026-06-25", set())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["entry"], 55.0)


class BuildClosedRow(unittest.TestCase):
    def _pos(self):
        return {"symbol": "2317", "setup": "BRK", "entry": 110.0,
                "sl": 100.0, "tp": 125.0, "entry_ts": "2026-06-25 10:00:00"}

    def test_row_length_matches_header(self):
        row = build_closed_row(self._pos(), "TP", 125.0, at(10, 30), 0.45)
        self.assertEqual(len(row), len(CLOSED_HEADER))

    def test_tp_row_fields(self):
        row = build_closed_row(self._pos(), "TP", 125.0, at(10, 30), 0.45)
        d = dict(zip(CLOSED_HEADER, row))
        self.assertEqual(d["symbol"], "2317")
        self.assertEqual(d["exit_reason"], "TP")
        self.assertEqual(float(d["exit_price"]), 125.0)
        self.assertAlmostEqual(float(d["gross_r"]), 1.5)

    def test_hold_secs_from_entry_to_close(self):
        row = build_closed_row(self._pos(), "TP", 125.0, at(10, 30), 0.45)
        d = dict(zip(CLOSED_HEADER, row))
        self.assertEqual(int(d["hold_secs"]), 1800)  # 10:00 → 10:30


if __name__ == "__main__":
    unittest.main()
