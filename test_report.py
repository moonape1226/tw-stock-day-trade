#!/usr/bin/env python3
import unittest

from report import summarize, filter_since


def row(close_time="2026-06-29 09:05:19", setup="PUL", reason="TP",
        gross_r="1.667", net_r="1.165", net_pnl_pct="1.04"):
    return {"close_time": close_time, "setup": setup, "exit_reason": reason,
            "gross_r": gross_r, "net_r": net_r, "net_pnl_pct": net_pnl_pct}


class TestSummarize(unittest.TestCase):
    def test_counts_wins_and_win_rate(self):
        rows = [row(net_r="1.0"), row(net_r="-0.5"), row(net_r="2.0"), row(net_r="-1.0")]
        s = summarize(rows)
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["wins"], 2)
        self.assertAlmostEqual(s["win_rate"], 0.5)

    def test_total_and_avg_net_r_expectancy(self):
        rows = [row(net_r="1.0"), row(net_r="-0.5"), row(net_r="2.5")]
        s = summarize(rows)
        self.assertAlmostEqual(s["total_net_r"], 3.0)
        self.assertAlmostEqual(s["avg_net_r"], 1.0)

    def test_cumulative_pnl_pct(self):
        rows = [row(net_pnl_pct="1.0"), row(net_pnl_pct="-0.4"), row(net_pnl_pct="0.6")]
        s = summarize(rows)
        self.assertAlmostEqual(s["cum_pnl_pct"], 1.2)

    def test_by_setup_groups_count_and_net_r(self):
        rows = [row(setup="PUL", net_r="1.0"), row(setup="BRK", net_r="-0.5"),
                row(setup="PUL", net_r="2.0")]
        s = summarize(rows)
        self.assertEqual(s["by_setup"]["PUL"]["n"], 2)
        self.assertAlmostEqual(s["by_setup"]["PUL"]["net_r"], 3.0)
        self.assertEqual(s["by_setup"]["BRK"]["n"], 1)

    def test_by_reason_counts(self):
        rows = [row(reason="TP"), row(reason="SL"), row(reason="TP"), row(reason="CLOSE")]
        s = summarize(rows)
        self.assertEqual(s["by_reason"], {"TP": 2, "SL": 1, "CLOSE": 1})

    def test_empty_returns_zero_without_error(self):
        s = summarize([])
        self.assertEqual(s["n"], 0)
        self.assertEqual(s["wins"], 0)
        self.assertEqual(s["win_rate"], 0.0)
        self.assertEqual(s["total_net_r"], 0.0)
        self.assertEqual(s["avg_net_r"], 0.0)


class TestFilterSince(unittest.TestCase):
    def test_keeps_on_or_after_date(self):
        rows = [row(close_time="2026-06-28 11:00:00"),
                row(close_time="2026-06-29 09:05:19"),
                row(close_time="2026-06-30 10:00:00")]
        out = filter_since(rows, "2026-06-29")
        self.assertEqual([r["close_time"][:10] for r in out], ["2026-06-29", "2026-06-30"])

    def test_none_returns_all(self):
        rows = [row(close_time="2026-06-28 11:00:00"), row(close_time="2026-06-29 09:05:19")]
        self.assertEqual(len(filter_since(rows, None)), 2)


if __name__ == "__main__":
    unittest.main()
