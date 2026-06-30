#!/usr/bin/env python3
"""
screen_v2 回測 — 驗證分桶是否有 edge (codex/claude/opencode 共識的 P-最後一項)

方法 (日線解析度,誠實版):
  對過去每個交易日 D,用 v2 邏輯(資料只到 D)把每檔分桶 A/B/C/D,並算 v1 分數;
  再看「隔日 D+1」的當沖結果 = 以 D+1 開盤進場:
    ret  = (D+1 收 - D+1 開)/開          當日持有到收
    MFE  = (D+1 高 - D+1 開)/開          最大有利
    MAE  = (D+1 低 - D+1 開)/開          最大不利
    win  = ret > 來回成本(0.45%)
  比較: v2 各桶 vs 全體基準 vs v1 風格 top-K。

資料源: FinMind TaiwanStockPrice 日線 (需 .env 的 FINMIND_API)。
籃子: screen_v2 目前的流動性選池前 N 檔 (固定籃子)。

限制(務必知道): 日線解析度無法判定盤中 TP/SL 誰先到;以隔日開盤進場、無滑價;
籃子取「當前」流動性股 → 有存活者偏誤;歷史處置股未排除;每桶每日樣本數不一。

用法: python3 backtest.py [--cap 60] [--days 60] [--k 5]
"""
import json, urllib.request, sys, time, math
from datetime import datetime, timezone, timedelta
from pathlib import Path
import screen_v2 as s   # 重用 build_universe 與參數

TZ = timezone(timedelta(hours=8))
COST = s.COST_PCT

ENV = {}
for _l in open(Path(__file__).parent / ".env"):
    if "=" in _l and not _l.strip().startswith("#"):
        _k, _v = _l.strip().split("=", 1); ENV[_k.strip()] = _v.strip().strip('"').strip("'")
FM_TOKEN = ENV.get("FINMIND_API", "")


def fm_daily(code, start):
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={code}&start_date={start}"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {FM_TOKEN}"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
        rows = d.get("data", [])
        out = []
        for x in rows:
            o, h, l, c = x.get("open"), x.get("max"), x.get("min"), x.get("close")
            v, m = x.get("Trading_Volume"), x.get("Trading_money")
            if None in (o, h, l, c, v, m) or c <= 0:
                continue
            out.append((x["date"], float(o), float(h), float(l), float(c), float(v), float(m)))
        out.sort(key=lambda r: r[0])
        return out
    except Exception as e:
        print(f"# {code} 抓取失敗: {e}", file=sys.stderr)
        return []


def bucket_and_scores(prevC, o, h, l, c, vol, money, avg20_vol, avg20_tv, atr20):
    """回傳 (v2_bucket, v2_score, v1_eligible, v1_score, metrics dict)。與 screen_v2.score_one 對齊。"""
    pct = (c - prevC) / prevC * 100
    gap = (o - prevC) / prevC * 100
    rng = (h - l) / prevC * 100
    pos = (c - l) / (h - l) if h > l else 0.5
    atr_pct = atr20 / prevC * 100 if atr20 > 0 else 0
    rvol = vol / avg20_vol if avg20_vol > 0 else 0
    limit = pct >= 9.4 and pos > 0.99

    rvol_score = max(0, min((rvol - 1) * 15, 30))
    vol_fit = 15 if s.ATR_LO <= atr_pct <= s.ATR_HI else (8 if s.ATR_HARD_LO <= atr_pct < s.ATR_HARD_HI else 0)
    strength = pos * 8 + min(max(pct, 0), 5)
    ext_pen = 0
    if gap > s.GAP_EXT: ext_pen -= 10
    if limit: ext_pen -= 15
    if pos > 0.98 and pct > 7: ext_pen -= 8
    v2_score = rvol_score + vol_fit + strength + ext_pen

    gate_ok = avg20_tv >= s.LIQ_GATE and s.PRICE_MIN <= c <= s.PRICE_MAX
    extended = gap > s.GAP_EXT or (pos > 0.95 and pct > 7) or limit
    if not gate_ok or limit or rvol < s.RVOL_MIN:
        bucket = "D"
    elif rvol >= s.RVOL_GATE and not extended and s.ATR_HARD_LO <= atr_pct <= s.ATR_HARD_HI:
        bucket = "A"
    elif pct > 0 and pos >= 0.6 and extended:
        bucket = "B"
    else:
        bucket = "C"

    # v1 風格 (screen.py): 收紅+收高+有振幅+量 gate,分數偏動能/收強
    v1_eligible = (gate_ok and not limit and pct > 0 and pos >= 0.6 and rng >= 3.0)
    v1_score = pos * 50 + min(pct, 9.9) * 4 + math.log10(money) if money > 0 else 0
    return bucket, round(v2_score, 1), v1_eligible, v1_score, pct


def agg(rows):
    """rows: list of (ret, mfe, mae, win). 回傳統計字串。"""
    n = len(rows)
    if n == 0:
        return "n=0"
    ret = sum(r[0] for r in rows) / n
    mfe = sum(r[1] for r in rows) / n
    mae = sum(r[2] for r in rows) / n
    win = sum(1 for r in rows if r[3]) / n * 100
    return f"n={n:<5} ret={ret:+.2f}%  win={win:4.1f}%  MFE={mfe:+.2f}%  MAE={mae:+.2f}%"


def main():
    args = sys.argv[1:]
    def opt(name, d):
        return int(args[args.index(name) + 1]) if name in args and args.index(name) + 1 < len(args) else d
    cap = opt("--cap", 60); days = opt("--days", 60); K = opt("--k", 5)

    start = (datetime.now(TZ) - timedelta(days=days + 60)).strftime("%Y-%m-%d")
    uni = s.build_universe()[:cap]
    print(f"# 籃子 {len(uni)} 檔 | 回看 ~{days} 交易日 | 進場=隔日開盤 | 成本 {COST}% | FinMind 日線", file=sys.stderr)

    # 收集所有 stock-day
    by_bucket = {"A": [], "B": [], "C": [], "D": []}
    by_regime = {"A_above": [], "A_below": [], "all_above": [], "all_below": []}
    all_rows = []
    per_day = {}   # date -> list of (v1_eligible, v1_score, v2_bucket, v2_score, outcome)
    for n, u in enumerate(uni, 1):
        h = fm_daily(u["code"], start)
        time.sleep(0.15)
        if len(h) < 24:
            continue
        for i in range(20, len(h) - 1):
            prevC = h[i - 1][4]
            _, o, hi, lo, c, vol, money = h[i]
            base = h[i - 20:i]
            ma20 = sum(r[4] for r in base) / 20   # 20 日收盤均線 (排除當日,與 screen_v2 對齊)
            avg20_vol = sum(r[5] for r in base) / 20
            avg20_tv = sum(r[6] for r in base) / 20
            trs = []
            for j in range(i - 20, i):
                trs.append(max(h[j][2] - h[j][3], abs(h[j][2] - h[j - 1][4]), abs(h[j][3] - h[j - 1][4])))
            atr20 = sum(trs) / len(trs)
            bkt, v2s, v1e, v1s, pct = bucket_and_scores(prevC, o, hi, lo, c, vol, money, avg20_vol, avg20_tv, atr20)
            # 隔日結果
            no = h[i + 1][1]
            if no <= 0:
                continue
            ret = (h[i + 1][4] - no) / no * 100
            mfe = (h[i + 1][2] - no) / no * 100
            mae = (h[i + 1][3] - no) / no * 100
            outcome = (ret, mfe, mae, ret > COST)
            by_bucket[bkt].append(outcome)
            all_rows.append(outcome)
            (by_regime["all_above"] if c >= ma20 else by_regime["all_below"]).append(outcome)
            if bkt == "A":
                (by_regime["A_above"] if c >= ma20 else by_regime["A_below"]).append(outcome)
            per_day.setdefault(h[i][0], []).append((v1e, v1s, bkt, v2s, outcome))

    print(f"\n=== 全體基準 (籃子所有 stock-day) ===\n  {agg(all_rows)}")
    print("\n=== v2 分桶 (隔日當沖表現) ===")
    for b in ("A", "B", "C", "D"):
        print(f"  [{b}] {agg(by_bucket[b])}")

    # 每日 top-K: v2-A(取分數前K) vs v1 風格(eligible 取分數前K)
    v2A_topk, v1_topk = [], []
    for date, lst in per_day.items():
        a = sorted([x for x in lst if x[2] == "A"], key=lambda x: -x[3])[:K]
        v2A_topk += [x[4] for x in a]
        v1 = sorted([x for x in lst if x[0]], key=lambda x: -x[1])[:K]
        v1_topk += [x[4] for x in v1]
    print(f"\n=== 每日 top-{K} 對決 ===")
    print(f"  v2 A 桶 top{K}: {agg(v2A_topk)}")
    print(f"  v1 風格 top{K}: {agg(v1_topk)}")

    # 簡短裁決
    def winrate(rows): return (sum(1 for r in rows if r[3]) / len(rows) * 100) if rows else 0
    def meanret(rows): return (sum(r[0] for r in rows) / len(rows)) if rows else 0
    base_ret = meanret(all_rows)
    print("\n=== 裁決 ===")
    a_ret = meanret(by_bucket["A"])
    print(f"  A 桶 ret {a_ret:+.2f}% vs 全體 {base_ret:+.2f}% → {'A 勝基準' if a_ret > base_ret else 'A 未勝基準'}")
    print(f"  v2A top{K} ret {meanret(v2A_topk):+.2f}% / win {winrate(v2A_topk):.0f}%  vs  "
          f"v1 top{K} ret {meanret(v1_topk):+.2f}% / win {winrate(v1_topk):.0f}%")

    print("\n=== MA20 順勢過濾驗證 (signal 日收盤 z vs MA20) ===")
    print(f"  全體 above MA20: {agg(by_regime['all_above'])}")
    print(f"  全體 below MA20: {agg(by_regime['all_below'])}")
    print(f"  A 桶 above MA20: {agg(by_regime['A_above'])}")
    print(f"  A 桶 below MA20: {agg(by_regime['A_below'])}")
    aa, ab = meanret(by_regime["A_above"]), meanret(by_regime["A_below"])
    wa, wb = winrate(by_regime["A_above"]), winrate(by_regime["A_below"])
    has_edge = (aa - ab > 0.1) and (wa - wb > 1)
    print(f"  → A 桶 above−below: ret 差 {aa - ab:+.2f}% / win 差 {wa - wb:+.0f}%  → "
          f"{'below 較差,過濾有 edge → 可加 z>=MA20 gate' if has_edge else 'edge 不足或反向 → 不建議加 MA20 過濾'}")
    print("\n注意: 日線解析度、隔日開盤進場、無滑價、籃子存活者偏誤、樣本有限——僅供方向性參考,非實盤期望值。")


if __name__ == "__main__":
    main()
