#!/usr/bin/env python3
"""
盤前量能基準匯出 — 為 watch.py 盤中 RVOL 提供每股 avg20 日均量。

盤前(開盤前)跑一次,把監控清單每檔的 20 日均量(張)寫成快照檔 avg20_vol.json,
watch.py 盤中即可用 今量/avg20_vol 估 RVOL,不必每股即時抓 20 日歷史。

基準為前一交易日(screen_v2.history 取最新一日之前的 20 日),
與 screen_v2 盤中口徑一致。

data 每檔含 avg20_vol(20 日均量,張)與 atr20(絕對價格單位)。

用法:
  python3 baseline.py                  # 讀 config.json 的 stocks
  python3 baseline.py --codes 2330,2317  # 追加額外代碼
"""
import json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import screen_v2 as s

TZ = timezone(timedelta(hours=8))
CONFIG = Path(__file__).parent / "config.json"
OUT = Path(__file__).parent / "avg20_vol.json"


def main():
    args = sys.argv[1:]
    extra = []
    if "--codes" in args:
        i = args.index("--codes")
        if i + 1 < len(args):
            extra = [c.strip() for c in args[i + 1].split(",") if c.strip()]

    cfg = json.loads(CONFIG.read_text())
    codes = [st["symbol"] for st in cfg.get("stocks", [])]
    for c in extra:
        if c not in codes:
            codes.append(c)

    now = datetime.now(TZ)
    this_ym = now.strftime("%Y%m")
    prev_ym = (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")

    print(f"# 盤前量能基準 {now:%Y-%m-%d} | {len(codes)} 檔 | 基準=前一交易日 20 日均量(張)", file=sys.stderr)
    data = {}
    skipped = []
    for code in codes:
        hist = s.history(code, this_ym, prev_ym)
        if not hist:
            skipped.append(code)
            print(f"# 略過 {code}: 抓不到 20 日歷史", file=sys.stderr)
            continue
        data[code] = {"avg20_vol": round(hist["avg20_vol"], 1), "atr20": round(hist["atr20"], 3)}

    OUT.write_text(json.dumps(
        {"date": now.strftime("%Y-%m-%d"), "data": data},
        ensure_ascii=False, indent=2))
    print(f"# 已寫入 {OUT.name}: {len(data)} 檔" + (f" | 略過 {len(skipped)} 檔: {','.join(skipped)}" if skipped else ""))


if __name__ == "__main__":
    main()
