#!/usr/bin/env python3
"""
三大法人買賣超 — 監控清單篩選
讀 config.json 的 stocks，抓指定日(預設今日)三大法人,標記外資買/賣超。
用法:
  python3 chips.py            # 今日 (TW)
  python3 chips.py 20260625   # 指定日 (YYYYMMDD, 須為交易日)
資料源: TWSE T86 三大法人買賣超日報 (免 token; 約 15:30-16:00 後公布)
"""
import json, urllib.request, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))
T86 = "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={date}&selectType=ALL"

# T86 欄位索引 (0-based): 4=外陸資買賣超, 10=投信買賣超, 18=三大法人買賣超 (股)
I_FOREIGN, I_TRUST, I_TOTAL = 4, 10, 18


def get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception as e:                       # C4: 網路/JSON 失敗不 traceback
        print(f"# 抓取/解析失敗: {e}", file=sys.stderr)
        return {}


def num(s):
    try:
        return int(str(s).replace(",", ""))
    except Exception:
        return 0


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime("%Y%m%d")
    try:                                         # C4: 驗證日期格式
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        print(f"日期格式錯誤: {date!r},需 YYYYMMDD")
        return
    with open(Path(__file__).parent / "config.json") as f:   # C3
        cfg = json.load(f)
    watch = {s["symbol"]: s["name"] for s in cfg["stocks"]}

    d = get(T86.format(date=date))
    if d.get("stat") != "OK" or not d.get("data"):
        print(f"{date}: 三大法人尚未公布 (或非交易日)。約 15:30-16:00 後再試。")
        return
    # DF1: 用欄位名稱定位,避免 TWSE 改欄序時靜默輸出錯誤數字
    fields = d.get("fields", [])
    def col(sub, default):
        return next((i for i, f in enumerate(fields) if sub in f), default)
    i_foreign = col("外陸資買賣超", I_FOREIGN)
    i_trust = col("投信買賣超", I_TRUST)
    i_total = col("三大法人買賣超", I_TOTAL)
    rows = {r[0].strip(): r for r in d["data"]}

    print(f"【{date} 三大法人買賣超 · 監控清單】(單位: 張)")
    keep, drop = [], []
    for sym, name in watch.items():
        r = rows.get(sym)
        if not r:
            print(f"  {sym} {name}: 無資料")
            continue
        frn = num(r[i_foreign]) // 1000
        trust = num(r[i_trust]) // 1000
        tot = num(r[i_total]) // 1000
        keep_it = frn > 0 or trust > 0   # 規則 B: 外資或投信任一買超即保留
        tag = "外資買超" if frn > 0 else ("投信買超(外資賣)" if trust > 0 else "外資投信皆未買超")
        print(f"  {sym} {name}  外資{frn:+,} 投信{trust:+,} 三大法人{tot:+,}  [{tag}]")
        (keep if keep_it else drop).append(f"{sym} {name}")

    print(f"\n建議保留(外資或投信買超 {len(keep)}): " + ("、".join(keep) or "無"))
    print(f"剔除(外資投信皆未買超 {len(drop)}): " + ("、".join(drop) or "無"))


if __name__ == "__main__":
    main()
