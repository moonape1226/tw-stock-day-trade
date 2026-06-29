#!/usr/bin/env python3
"""
paper trade 績效彙總 — 讀 trades_closed.csv,印勝率 / 期望值(avg R) / by setup / 累計損益。

用法:
  python3 report.py                  # 全部已平倉交易
  python3 report.py --since 2026-06-29   # 只看該日(含)之後
資料源: trades_closed.csv (track.py 產出)。
"""
import csv, sys
from collections import Counter
from pathlib import Path

CLOSED_LOG = Path(__file__).parent / "trades_closed.csv"


def filter_since(rows, since):
    """保留 close_time 日期 >= since (YYYY-MM-DD) 的列; since=None 回傳全部。"""
    if not since:
        return list(rows)
    return [r for r in rows if r["close_time"][:10] >= since]


def summarize(rows):
    """彙總交易列。回傳 dict: n / wins / win_rate / total_net_r / avg_net_r /
    total_gross_r / cum_pnl_pct / by_setup / by_reason。空輸入回傳零值不報錯。"""
    n = len(rows)
    if not n:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "total_net_r": 0.0,
                "avg_net_r": 0.0, "total_gross_r": 0.0, "cum_pnl_pct": 0.0,
                "by_setup": {}, "by_reason": {}}
    wins = sum(1 for r in rows if float(r["net_r"]) > 0)
    total_net_r = sum(float(r["net_r"]) for r in rows)
    by_setup = {}
    for r in rows:
        g = by_setup.setdefault(r["setup"], {"n": 0, "net_r": 0.0})
        g["n"] += 1
        g["net_r"] += float(r["net_r"])
    return {
        "n": n,
        "wins": wins,
        "win_rate": wins / n,
        "total_net_r": total_net_r,
        "avg_net_r": total_net_r / n,
        "total_gross_r": sum(float(r["gross_r"]) for r in rows),
        "cum_pnl_pct": sum(float(r["net_pnl_pct"]) for r in rows),
        "by_setup": by_setup,
        "by_reason": dict(Counter(r["exit_reason"] for r in rows)),
    }


def main():
    args = sys.argv[1:]
    since = None
    if "--since" in args:
        i = args.index("--since")
        if i + 1 < len(args):
            since = args[i + 1]

    if not CLOSED_LOG.exists():
        print("trades_closed.csv 不存在 — 尚無已平倉交易")
        return

    rows = filter_since(list(csv.DictReader(open(CLOSED_LOG))), since)
    s = summarize(rows)
    scope = f" (since {since})" if since else ""
    if not s["n"]:
        print(f"無已平倉交易{scope}")
        return

    print(f"# paper 績效{scope} — {CLOSED_LOG.name}")
    print(f"筆數 {s['n']} | 勝率 {s['win_rate']*100:.0f}% ({s['wins']}/{s['n']}) | "
          f"總 net R {s['total_net_r']:+.2f} | 期望值 {s['avg_net_r']:+.2f}R/筆 | "
          f"累計淨損益 {s['cum_pnl_pct']:+.2f}%")
    print(f"出場原因: {s['by_reason']}")
    print("依 setup:")
    for setup, g in sorted(s["by_setup"].items()):
        print(f"  {setup:<5} {g['n']:>3} 筆 | net R {g['net_r']:+.2f} | "
              f"平均 {g['net_r']/g['n']:+.2f}R")


if __name__ == "__main__":
    main()
