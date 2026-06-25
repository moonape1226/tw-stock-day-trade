#!/usr/bin/env python3
"""
台股當沖/短線 選股篩選器
方法:
  1. 官方全市場日線 (STOCK_DAY_ALL) 取「流動性 + 波動」選池 (慢變特性)
  2. MIS 即時/收盤報價算「今日動能 + 收盤強度」(當下訊號)
  3. 偏多排序: 收紅 + 收在當日高 + 有量有振幅

用法:
  python3 screen.py                      # 印出排序候選 (前 20)
  python3 screen.py --top 30
  python3 screen.py --exclude 2327,2404  # 排除特定代碼
  python3 screen.py --tradeable          # 只留可當沖 (排除漲停鎖死/注意處置)
  python3 screen.py --json               # 輸出 JSON (給程式串接)

資料源 (與 TWSEMCPServer 同底層, 免 token):
  https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL  上市全市場日線 (EOD, T 或 T-1)
  https://mis.twse.com.tw/stock/api/getStockInfo.jsp           即時/收盤報價
"""

import json, urllib.request, time, re, math, sys, unicodedata

OPENAPI = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

# ── 篩選參數 ──────────────────────────────────────────
MIN_TURNOVER = 2e8     # 選池: 前一交易日成交金額下限 (流動性, 元)
MIN_RANGE_PCT = 3.0    # 選池 & 今日: 振幅下限 (波動, %)
PRICE_MIN, PRICE_MAX = 10, 1500
MIN_CLOSE_POS = 0.6    # 今日: 收盤位於當日 (high-low) 區間的下限 (收強)
MIN_PCT_TODAY = 0.0    # 今日: 漲跌幅下限 (收紅)
UNIVERSE_CAP = 120     # 選池最多取流動性前 N 檔
LIMIT_PCT = 9.4        # 漲停 fallback 判定 (MIS 無漲停價時用; 隔日沖性質, 難當沖)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def fnum(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def build_universe():
    """官方日線 → 流動性+波動選池。回傳 (eod_date, [ {code,name,turnover} ])"""
    try:                                          # C2: TWSE 維護/429/非 JSON 不 traceback
        rows = json.loads(get(OPENAPI))
    except Exception as e:
        print(f"# STOCK_DAY_ALL 抓取/解析失敗: {e}", file=sys.stderr)
        return "", []
    eod_date = rows[0].get("Date", "") if rows else ""
    uni = []
    for r in rows:
        code = r["Code"]
        if not re.fullmatch(r"[1-9]\d{3}", code):   # 只留一般上市股 (排除 ETF/權證)
            continue
        C = fnum(r["ClosingPrice"]); H = fnum(r["HighestPrice"]); L = fnum(r["LowestPrice"])
        CH = fnum(r["Change"]); TV = fnum(r["TradeValue"])
        if None in (C, H, L, CH, TV) or C <= 0:
            continue
        prev = C - CH
        if prev <= 0:
            continue
        rng = (H - L) / prev * 100
        if TV >= MIN_TURNOVER and rng >= MIN_RANGE_PCT and PRICE_MIN <= C <= PRICE_MAX:
            uni.append({"code": code, "name": r["Name"].strip(), "turnover": TV})
    uni.sort(key=lambda x: -x["turnover"])
    return eod_date, uni[:UNIVERSE_CAP]


def fetch_today(codes):
    """MIS 批次抓今日報價。回傳 {code: msgArray item}"""
    out = {}
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        exch = "|".join(f"tse_{c}.tw" for c in chunk)
        try:
            m = json.loads(get(f"{MIS}?ex_ch={exch}&json=1&delay=0"))
            for it in m.get("msgArray", []):
                out[it.get("c")] = it
        except Exception as e:
            print(f"# MIS chunk error: {e}", file=sys.stderr)
        time.sleep(0.5)
    return out


def screen(exclude=frozenset()):
    """回傳 (eod_date, [候選 dict, 依 score 由高到低])"""
    eod_date, uni = build_universe()
    today = fetch_today([u["code"] for u in uni])
    cands = []
    for u in uni:
        if u["code"] in exclude:
            continue
        it = today.get(u["code"])
        if not it:
            continue
        z = fnum(it.get("z")); y = fnum(it.get("y")); h = fnum(it.get("h")); l = fnum(it.get("l"))
        nm = it.get("n", "").strip()
        if None in (z, y, h, l) or y <= 0 or h <= l:
            continue
        pct = (z - y) / y * 100
        rng = (h - l) / y * 100
        pos = (z - l) / (h - l)
        if pct < MIN_PCT_TODAY or pos < MIN_CLOSE_POS or rng < MIN_RANGE_PCT:
            continue
        flags = []
        limit_up = fnum(it.get("u"))    # MIS 漲停價; 收在漲停 = 鎖死
        if (limit_up and z >= limit_up - 1e-6) or (pct >= LIMIT_PCT and pos >= 0.99):
            flags.append("LIMIT")    # 漲停 (隔日沖性質, 難當沖)
        if "*" in nm:
            flags.append("WATCH")    # 注意/處置/全額交割等交易限制
        score = pos * 50 + min(pct, 9.9) * 4 + math.log10(u["turnover"])
        cands.append({
            "code": u["code"], "name": u["name"], "close": round(z, 2),
            "pct": round(pct, 2), "range": round(rng, 1), "pos": round(pos, 2),
            "turnover": u["turnover"], "flags": flags, "score": round(score, 1),
        })
    cands.sort(key=lambda x: -x["score"])
    return eod_date, cands


def _vw(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def main():
    args = sys.argv[1:]
    top = 20
    exclude = set()
    tradeable_only = False
    as_json = "--json" in args
    if "--tradeable" in args:
        tradeable_only = True
    for i, a in enumerate(args):
        if a == "--top" and i + 1 < len(args):
            top = int(args[i + 1])
        if a == "--exclude" and i + 1 < len(args):
            exclude = {c.strip() for c in args[i + 1].split(",") if c.strip()}

    eod_date, cands = screen(exclude)
    if tradeable_only:
        cands = [c for c in cands if not c["flags"]]
    cands = cands[:top]

    if as_json:
        print(json.dumps(cands, ensure_ascii=False, indent=2))
        return

    print(f"# 選池日線={eod_date} (ROC) | 今日報價=MIS | 候選 {len(cands)} 檔 "
          f"(門檻: 量>={MIN_TURNOVER/1e8:.0f}億 振幅>={MIN_RANGE_PCT}% 收位>={MIN_CLOSE_POS:.0%})")
    print(f"{'代碼':<6}{'名稱':<10}{'收盤':>9}{'漲%':>7}{'振%':>6}{'收位':>6}{'量(億)':>9}  旗標")
    for c in cands:
        nm = c["name"]; pad = max(0, 10 - _vw(nm))
        flag = " ".join(c["flags"])
        print(f"{c['code']:<6}{nm}{' '*pad}{c['close']:>9.1f}{c['pct']:>7.2f}"
              f"{c['range']:>6.1f}{c['pos']*100:>5.0f}%{c['turnover']/1e8:>8.1f}  {flag}")


if __name__ == "__main__":
    main()
