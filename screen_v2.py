#!/usr/bin/env python3
"""
台股當沖選股篩選器 v2 — 修正 v1 評分偏誤 (codex/claude/opencode 討論收斂)

v1 的問題: log10(turnover) 幾乎是常數 → 「流動性優先」名實不符;min(pct)×4 反而獎勵接近
漲停;缺 RVOL/ATR;振幅被雙重門檻計分。v2 改為:

  流動性 = 二元 GATE (avg20 成交額 ≥ 門檻),不進排序
  排序分數 = RVOL + ATR 適配 + 收盤強度(降權) + extension 懲罰
  分桶: A 核心可執行 / B 動能延伸(等拉回) / C 邊際 / D 排除(漲停/流動性/RVOL 不足)

資料源 (免 token):
  STOCK_DAY_ALL  全市場日線 → 選池 + 前日量 gate
  STOCK_DAY      個股月檔 → avg20 量/額、ATR20 (快取於 .cache/)
  MIS getStockInfo  今日收盤 + 今日量 v(張) + 漲停價 u

未涵蓋(另案): 上櫃 TPEx、官方禁當沖清單(需 MCP)、kbar 回測驗證、先賣後買空方。

用法:
  python3 screen_v2.py              # 預設掃前 50 大流動性
  python3 screen_v2.py --cap 80
  python3 screen_v2.py --full       # 不限檔數(較慢,首次)
  python3 screen_v2.py --json
"""
import json, urllib.request, time, re, sys, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))
ALLDAY = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
STOCK_DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={ym}01&stockNo={code}&response=json"
MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
CACHE = Path(__file__).parent / ".cache"

# ── 參數 ──────────────────────────────────────────────
LIQ_GATE = 2e8        # avg20 成交額 gate (元)
PRICE_MIN, PRICE_MAX = 10, 2000
RVOL_GATE = 1.5       # 進 A 桶的 RVOL 下限
RVOL_MIN = 1.0        # 低於此 → D 桶 (無異常活躍)
ATR_LO, ATR_HI = 2.0, 7.0       # ATR% 甜蜜帶 (滿分)
ATR_HARD_LO, ATR_HARD_HI = 1.5, 8.0   # 可接受帶
GAP_EXT = 4.0         # 跳空 % 超過視為延伸
COST_PCT = 0.45       # 當沖來回成本估計 (供判讀)
DEFAULT_CAP = 50

# 盤中累計成交量約占全日比例 (U 形, 近似值, 待 server 實測校正)
INTRADAY_VOL_CURVE = [(9,30,0.25),(10,0,0.38),(10,30,0.48),(11,0,0.57),(11,30,0.66),(12,0,0.74),(12,30,0.83),(13,0,0.92),(13,30,1.0)]


def expected_vol_fraction(now):
    hm = now.hour*60 + now.minute
    if hm <= 9*60: return 0.02
    for h,m,f in INTRADAY_VOL_CURVE:
        if hm <= h*60+m: return f
    return 1.0


def get_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"# 抓取失敗 {url[:60]}...: {e}", file=sys.stderr)
        return {}


def fnum(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None


def stock_day(code, ym):
    """個股某月日線 (快取)。回傳 [(date, vol_lots, turnover, o,h,l,c)]。"""
    cur_ym = datetime.now(TZ).strftime("%Y%m")
    is_cur = ym == cur_ym   # 當月檔每天增長,不快取以涵蓋最新交易日
    CACHE.mkdir(exist_ok=True)
    cf = CACHE / f"sd_{code}_{ym}.json"
    if not is_cur and cf.exists():
        try:
            return json.loads(cf.read_text())
        except Exception:
            pass
    d = get_json(STOCK_DAY.format(ym=ym, code=code))
    out = []
    if d.get("stat") == "OK":
        for r in d.get("data", []):
            vol = fnum(r[1]); tv = fnum(r[2]); o = fnum(r[3]); h = fnum(r[4]); l = fnum(r[5]); c = fnum(r[6])
            if None in (vol, tv, o, h, l, c):
                continue
            out.append([r[0], vol / 1000.0, tv, o, h, l, c])   # vol → 張
    if not is_cur and out:   # 空結果不快取: 避免 prev_ym 抓取失敗被永久毒化(再也不重試)
        cf.write_text(json.dumps(out))
    time.sleep(0.25)   # 對 TWSE 客氣一點
    return out


def history(code, this_ym, prev_ym):
    """近 20 個交易日基準 (排除今日)。回傳 dict 或 None。"""
    rows = stock_day(code, prev_ym) + stock_day(code, this_ym)
    if len(rows) < 22:
        return None
    rows = rows[:-1]          # 排除最新一日(今日),用其前 20 日做基準
    base = rows[-20:]
    avg20_tv = sum(r[2] for r in base) / 20
    avg20_vol = sum(r[1] for r in base) / 20
    # ATR20: TR = max(h-l, |h-prevC|, |l-prevC|)
    trs = []
    for i in range(len(rows) - 20, len(rows)):
        h, l, c = rows[i][4], rows[i][5], rows[i][6]
        pc = rows[i - 1][6]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr20 = sum(trs) / len(trs)
    return {"avg20_tv": avg20_tv, "avg20_vol": avg20_vol, "atr20": atr20}


def disposal_codes(target):
    """處置股代號集合 (處置期間涵蓋 target='YYYYMMDD' 日)。處置=人工撮合,不適合當沖。"""
    d = get_json("https://www.twse.com.tw/rwd/zh/announcement/punish?response=json")
    out = set()
    if not isinstance(d, dict) or d.get("stat") != "OK":
        return out
    tgt = int(target)
    for r in d.get("data", []):
        m = re.findall(r"(\d{2,3})/(\d{2})/(\d{2})", str(r[6]))   # 處置起迄時間
        if len(m) >= 2:
            (sy, sm, sd), (ey, em, ed) = m[0], m[1]
            start = (int(sy) + 1911) * 10000 + int(sm) * 100 + int(sd)
            end = (int(ey) + 1911) * 10000 + int(em) * 100 + int(ed)
            if start <= tgt <= end:
                out.add(str(r[2]).strip())
    return out


def build_universe():
    """STOCK_DAY_ALL → 前日量 gate 的選池 (便宜預篩,稍後用 avg20 再 gate)。"""
    rows = get_json(ALLDAY)
    if not isinstance(rows, list):
        return []
    uni = []
    for r in rows:
        code = r.get("Code", "")
        if not re.fullmatch(r"[1-9]\d{3}", code):
            continue
        C = fnum(r["ClosingPrice"]); TV = fnum(r["TradeValue"])
        if C is None or TV is None or not (PRICE_MIN <= C <= PRICE_MAX):
            continue
        if TV >= LIQ_GATE:
            uni.append({"code": code, "name": r["Name"].strip(), "prev_tv": TV})
    uni.sort(key=lambda x: -x["prev_tv"])
    return uni


def fetch_today(codes):
    out = {}
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        exch = "|".join(f"tse_{c}.tw" for c in chunk)
        m = get_json(f"{MIS}?ex_ch={exch}&json=1&delay=0")
        for it in m.get("msgArray", []):
            out[it.get("c")] = it
        time.sleep(0.4)
    return out


def score_one(u, it, hist, frac, disposed=False):
    z = fnum(it.get("z")); o = fnum(it.get("o")); h = fnum(it.get("h"))
    l = fnum(it.get("l")); y = fnum(it.get("y")); v = fnum(it.get("v")); up = fnum(it.get("u"))
    if None in (z, o, h, l, y, v) or y <= 0 or h < l:
        return None
    pct = (z - y) / y * 100
    gap = (o - y) / y * 100
    rng = (h - l) / y * 100
    pos = (z - l) / (h - l) if h > l else 0.5
    atr_pct = hist["atr20"] / y * 100 if hist["atr20"] > 0 else 0
    rvol = v / (hist["avg20_vol"] * frac) if hist["avg20_vol"] > 0 else 0
    limit = bool(up and z >= up - 1e-6)

    rvol_score = max(0, min((rvol - 1) * 15, 30))
    vol_fit = 15 if ATR_LO <= atr_pct <= ATR_HI else (8 if ATR_HARD_LO <= atr_pct < ATR_HARD_HI else 0)
    strength = pos * 8 + min(max(pct, 0), 5)
    ext_pen = 0
    if gap > GAP_EXT: ext_pen -= 10
    if limit: ext_pen -= 15
    if pos > 0.98 and pct > 7: ext_pen -= 8
    score = rvol_score + vol_fit + strength + ext_pen

    gate_ok = hist["avg20_tv"] >= LIQ_GATE and PRICE_MIN <= z <= PRICE_MAX
    extended = gap > GAP_EXT or (pos > 0.95 and pct > 7) or limit
    if disposed or not gate_ok or limit or rvol < RVOL_MIN:
        bucket = "D"
    elif rvol >= RVOL_GATE and not extended and ATR_HARD_LO <= atr_pct <= ATR_HARD_HI:
        bucket = "A"
    elif pct > 0 and pos >= 0.6 and extended:
        bucket = "B"
    else:
        bucket = "C"

    return {"code": u["code"], "name": u["name"], "z": z, "pct": pct, "rvol": rvol,
            "atr_pct": atr_pct, "rng": rng, "gap": gap, "pos": pos, "limit": limit,
            "disposed": disposed, "avg20_tv": hist["avg20_tv"],
            "score": round(score, 1), "bucket": bucket}


def _vw(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def main():
    args = sys.argv[1:]
    cap = DEFAULT_CAP
    if "--full" in args: cap = 10_000
    if "--cap" in args:
        i = args.index("--cap")
        if i + 1 < len(args): cap = int(args[i + 1])
    as_json = "--json" in args

    now = datetime.now(TZ)
    this_ym = now.strftime("%Y%m")
    prev_ym = (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
    frac = expected_vol_fraction(now)

    uni = build_universe()[:cap]
    disposed = disposal_codes(now.strftime("%Y%m%d"))   # #2: 處置股 gate
    print(f"# 選池(前日量≥{LIQ_GATE/1e8:.0f}億): {len(uni)} 檔 | 處置股 {len(disposed)} 檔 | 抓 20 日歷史中…", file=sys.stderr)
    today = fetch_today([u["code"] for u in uni])

    cands = []
    for u in uni:
        it = today.get(u["code"])
        if not it:
            continue
        hist = history(u["code"], this_ym, prev_ym)
        if not hist:
            continue
        c = score_one(u, it, hist, frac, u["code"] in disposed)
        if c:
            cands.append(c)

    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    cands.sort(key=lambda x: (order[x["bucket"]], -x["score"]))

    if as_json:
        print(json.dumps([c for c in cands if c["bucket"] != "D"], ensure_ascii=False, indent=2))
        return

    labels = {"A": "核心可執行", "B": "動能延伸(等拉回)", "C": "邊際", "D": "排除"}
    print(f"# 日期 {now:%Y-%m-%d} | 流動性=GATE(avg20額≥{LIQ_GATE/1e8:.0f}億) | "
          f"當沖來回成本~{COST_PCT}% | RVOL=今量/(20日均量×frac) | ATR%=ATR20/昨收 | 時段量比 frac={frac:.2f}")
    for b in ("A", "B", "C"):
        rows = [c for c in cands if c["bucket"] == b]
        print(f"\n[{b}] {labels[b]} — {len(rows)} 檔")
        if not rows:
            print("  (無)"); continue
        print(f"  {'代碼':<6}{'名稱':<10}{'收盤':>9}{'漲%':>7}{'RVOL':>6}{'ATR%':>6}{'缺口%':>7}{'收位':>6}{'分數':>7}")
        for c in rows:
            nm = c["name"]; pad = max(0, 10 - _vw(nm))
            print(f"  {c['code']:<6}{nm}{' '*pad}{c['z']:>9.1f}{c['pct']:>7.2f}"
                  f"{c['rvol']:>6.1f}{c['atr_pct']:>6.1f}{c['gap']:>7.2f}{c['pos']*100:>5.0f}%{c['score']:>7.1f}")
    nd = len([c for c in cands if c["bucket"] == "D"])
    disp = [f"{c['code']} {c['name']}" for c in cands if c.get("disposed")]
    print(f"\n[D] {labels['D']}: {nd} 檔 (處置/漲停/流動性不足/RVOL<{RVOL_MIN})")
    if disp:
        print("    其中處置股(不可當沖): " + "、".join(disp))


if __name__ == "__main__":
    main()
