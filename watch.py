#!/usr/bin/env python3
"""
台股當沖即時監控面板 v11 — FinMind 即時資料
用法: python3 watch.py [--once]

v11: 移除部位追蹤 (positions.json / 持倉區 / 觸價提醒)。
     面板只負責進場訊號 (Action) 與 paper_trades.csv 記錄，部位管理改回人工。
     13:15 後仍以 [CLOSE] 停止產生新進場訊號。
"""

import json, urllib.request, urllib.error, time, sys, os, unicodedata, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import namedtuple

TZ = timezone(timedelta(hours=8))
FM_SNAP = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"

# ── Load .env ────────────────────────────────────────
def load_env():
    env = {}
    p = Path(__file__).parent / ".env"
    if p.exists():
        with open(p) as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = load_env()
FM_TOKEN = ENV.get("FINMIND_API", "") or ENV.get("FINDMIND_API", "")

# ── ANSI ──────────────────────────────────────────────
def _c(s, code): return f"\033[{code}m{s}\033[0m"
gr = lambda s: _c(s, 32); rd = lambda s: _c(s, 31); yl = lambda s: _c(s, 33)
cy = lambda s: _c(s, 36); mg = lambda s: _c(s, 35); bo = lambda s: _c(s, 1)
di = lambda s: _c(s, 2)

def vis_width(s):
    w = 0; skip = False
    for ch in s:
        if ch == '\033': skip = True; continue
        if skip:
            if ch == 'm': skip = False
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ('W','F') else 1
    return w

def pad_left(s, w): return s + ' ' * max(0, w - vis_width(s))

# ── FinMind API ──────────────────────────────────────

_last_error = ""

def fetch_snapshot(data_id):
    global _last_error
    try:
        url = f"{FM_SNAP}?data_id={data_id}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {FM_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        rows = d.get("data", [])
        if not rows:
            _last_error = f"{data_id}: empty"
            return None
        row = rows[0]
        return {
            "p": float(row.get("close", 0)),
            "prev": float(row.get("close", 0)) - float(row.get("change_price", 0)),
            "vwap": float(row.get("average_price", 0) or 0),
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "vol": int(row.get("total_volume", 0)),
            "vol_ratio": float(row.get("volume_ratio", 0) or 0),
            "chg": float(row.get("change_price", 0)),
            "chg_pct": float(row.get("change_rate", 0) or 0),
            "ts": row.get("date", ""),
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:80]
        _last_error = f"{data_id}: HTTP {e.code} {body}"
        return None
    except Exception as e:
        _last_error = f"{data_id}: {e}"
        return None

# ── 開盤區間 ──────────────────────────────────────────

_opening_range = {}
_prev_snapshots = {}
_brk_confirm = {}  # sym -> consecutive breakout confirmations

def update_opening_range(sym, snap):
    if snap is None: return
    now = datetime.now(TZ)
    or_data = _opening_range.get(sym)

    if or_data and or_data.get("frozen") and now.hour == 9 and now.minute < 30:
        or_data = None

    if or_data is None:
        if now.hour < 9:
            or_data = {"or_h": snap["open"], "or_l": snap["open"], "frozen": False}
        elif now.hour == 9 and now.minute < 30:
            or_data = {"or_h": snap["high"], "or_l": snap["low"], "frozen": False}
        else:
            or_data = {"or_h": snap["high"], "or_l": snap["low"], "frozen": True}
        _opening_range[sym] = or_data

    if not or_data["frozen"]:
        if now.hour == 9 and now.minute < 30:
            or_data["or_h"] = max(or_data["or_h"], snap["high"])
            or_data["or_l"] = min(or_data["or_l"], snap["low"])
        elif now.hour >= 9:
            or_data["frozen"] = True

def get_opening_range(sym):
    d = _opening_range.get(sym)
    if d: return d["or_l"], d["or_h"]
    return None, None

_or_path = Path(__file__).parent / "opening_range.json"

def load_opening_range(today):
    """讀回開盤區間; 僅當持久化日期 == today 才續用，否則回空 dict (跨日)。"""
    if not _or_path.exists():
        return {}
    try:
        d = json.loads(_or_path.read_text())
    except Exception:
        return {}
    if d.get("date") == today:
        return d.get("ranges", {})
    return {}

def save_opening_range(today):
    try:
        _or_path.write_text(json.dumps({"date": today, "ranges": _opening_range}, ensure_ascii=False))
    except Exception as e:
        print(f"# 開盤區間寫入失敗: {e}", file=sys.stderr)

# ── 盤中 RVOL 量能基準 ─────────────────────────────────

# 盤中累計成交量約占全日比例 (U 形, 近似值, 待 server 實測校正)。
# 與 screen_v2.INTRADAY_VOL_CURVE 對應; 兩支為獨立程式故複製一份。
INTRADAY_VOL_CURVE = [(9,30,0.25),(10,0,0.38),(10,30,0.48),(11,0,0.57),(11,30,0.66),(12,0,0.74),(12,30,0.83),(13,0,0.92),(13,30,1.0)]


def expected_vol_fraction(now):
    hm = now.hour*60 + now.minute
    if hm <= 9*60: return 0.02
    for h,m,f in INTRADAY_VOL_CURVE:
        if hm <= h*60+m: return f
    return 1.0


def intraday_rvol(cum_lots, avg20_lots, frac):
    """今累計量(張)/(20 日均量(張)×時段比例) → 真 RVOL。基準缺/時段為 0 → 0.0 防呆。
    FinMind total_volume 與 avg20_lots 同單位=張(2408 實測 snapshot 108299 == MIS v 108299),不換算。"""
    if avg20_lots <= 0 or frac <= 0:
        return 0.0
    return cum_lots / (avg20_lots * frac)


_vol_baseline = {}

def load_baseline(today):
    """讀 avg20_vol.json,僅當 json 的 date==today 才載入其 data;否則回空 dict 並警告。"""
    p = Path(__file__).parent / "avg20_vol.json"
    if not p.exists():
        print("[RVOL] avg20_vol.json 缺,將 fallback FinMind volume_ratio")
        return {}
    try:
        with open(p) as f:
            j = json.load(f)
    except Exception as e:
        print(f"[RVOL] avg20_vol.json 讀取失敗 ({e}),將 fallback FinMind volume_ratio")
        return {}
    if j.get("date") != today:
        print(f"[RVOL] 量能基準過期 (json {j.get('date')} != {today}),將 fallback FinMind volume_ratio")
        return {}
    return j.get("data", {})

# ── 綜合分析 ──────────────────────────────────────────

def stock_vwap_dir(price, vwap, prev_price):
    """價格相對 VWAP 位置 + 與上一 snapshot 的價格動能 → 方向 (1/0/-1)"""
    if prev_price is None or prev_price <= 0:
        return 1 if price > vwap else (-1 if price < vwap else 0)
    if price > vwap and price >= prev_price:
        return 1
    if price < vwap and price <= prev_price:
        return -1
    return 0

def analyze_stock(sym, snap, prev_snap, cfg):
    if snap is None: return None
    price = snap["p"]
    vwap = snap["vwap"]
    if vwap == 0:
        vwap = (snap["open"] + snap["high"] + snap["low"] + price) / 4

    prev_price = prev_snap["p"] if prev_snap else None
    vd = stock_vwap_dir(price, vwap, prev_price)

    or_l, or_h = get_opening_range(sym)
    if or_l is None:
        or_l, or_h = snap["low"], snap["high"]

    dp = (price - vwap) / vwap * 100 if vwap > 0 else 0

    mom_pct = 0
    if prev_snap and prev_snap["p"] > 0:
        mom_pct = (price - prev_snap["p"]) / prev_snap["p"] * 100

    prev_close = snap["prev"]
    chg_pct = snap["chg_pct"]
    if prev_close == 0: chg_pct = 0
    elif chg_pct == 0:
        chg_pct = (price - prev_close) / prev_close * 100

    # 量能門檻改用真 RVOL: 今累計量/(avg20×時段比例)。基準缺/過期 → fallback FinMind volume_ratio。
    base = _vol_baseline.get(sym)
    if base:
        avg20 = base.get("avg20_vol", 0)
        if avg20:
            vr = intraday_rvol(snap["vol"], avg20, expected_vol_fraction(datetime.now(TZ)))
            vr_src = "rvol"
        else:
            vr = snap["vol_ratio"]
            vr_src = "finmind"
        atr20 = base.get("atr20")
        atr_pct = atr20 / snap["prev"] * 100 if (atr20 and snap["prev"] > 0) else None
    else:
        vr = snap["vol_ratio"]
        vr_src = "finmind"
        atr_pct = None

    return {
        "p": price, "prev": prev_close, "vwap": vwap, "vd": vd,
        "dp": dp, "or_h": or_h, "or_l": or_l,
        "dh": snap["high"], "dl": snap["low"],
        "mom": mom_pct, "vr": vr, "vr_src": vr_src, "atr_pct": atr_pct,
        "ts": snap["ts"], "chg_pct": chg_pct, "vol": snap["vol"],
    }

def analyze_index(idx_sym, snap, prev_snap, cfg):
    if snap is None: return None
    price = snap["p"]
    open_price = snap["open"]
    prev_close = snap["prev"]
    vwap = prev_close if prev_close > 0 else open_price

    vd = 0
    if prev_snap and prev_snap["p"] > 0:
        if price > prev_snap["p"] * 1.0001: vd = 1
        elif price < prev_snap["p"] * 0.9999: vd = -1
    else:
        if price > open_price * 1.0002: vd = 1
        elif price < open_price * 0.9998: vd = -1

    dp = (price - vwap) / vwap * 100 if vwap > 0 else 0
    mom_pct = 0
    if prev_snap and prev_snap["p"] > 0:
        mom_pct = (price - prev_snap["p"]) / prev_snap["p"] * 100
    crash = mom_pct < -0.5

    chg_pct = snap["chg_pct"]
    if prev_close == 0: chg_pct = 0
    elif chg_pct == 0:
        chg_pct = (price - prev_close) / prev_close * 100

    return {
        "p": price, "prev": prev_close, "vwap": vwap, "vd": vd,
        "dp": dp, "or_h": snap["high"], "or_l": snap["low"],
        "dh": snap["high"], "dl": snap["low"],
        "mom": mom_pct, "vr": snap["vol_ratio"],
        "crash": crash, "ts": snap["ts"], "chg_pct": chg_pct,
    }

# ── 策略 ──────────────────────────────────────────────

EXT_THRESHOLD = 2.0
EXT_K = 0.6
EXT_FLOOR = 1.0
EXT_CEIL = 4.0
STALE_AGE_SECONDS = 60  # 資料超過 60 秒視為 stale

def ext_threshold(atr_pct):
    """過度延伸門檻(% dVWAP)。無 ATR 基準 → 沿用固定 EXT_THRESHOLD;否則 k×atr_pct 夾在 [FLOOR,CEIL]。"""
    if atr_pct is None or atr_pct <= 0: return EXT_THRESHOLD
    return min(EXT_CEIL, max(EXT_FLOOR, EXT_K * atr_pct))

def tick_size(price):
    """台股 tick size"""
    if price < 10: return 0.01
    if price < 50: return 0.05
    if price < 100: return 0.1
    if price < 500: return 0.5
    if price < 1000: return 1.0
    return 5.0

def round_to_tick(price, tick):
    """四捨五入到最近 tick"""
    return round(price / tick) * tick

def age_seconds(d, now):
    """回傳資料年齡（秒），無法解析回傳 999"""
    if not d or not d.get("ts"):
        return 999
    try:
        t = datetime.strptime(d["ts"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        return max(0, (now - t).total_seconds())
    except Exception:
        return 999

def update_brk_confirm(sym, d, cfg):
    """更新 BRK 連續確認計數 (在資料更新階段呼叫，不在 render)"""
    s = cfg["strategy"]
    brk_raw = d["p"] > d["or_h"] and d["vr"] > s["breakout_volume_ratio"]
    if brk_raw:
        _brk_confirm[sym] = _brk_confirm.get(sym, 0) + 1
    else:
        _brk_confirm[sym] = 0

# evaluate() 回傳欄位具名,避免 9 元素 positional tuple 錯位 (D1)
Eval = namedtuple("Eval", "sg f1 f2a f2b f3 detail reason setup risk")

def evaluate(d, idx, cfg, now, sym):
    """回傳 Eval(...);純函數:不寫狀態,僅唯讀全域 _brk_confirm (DOC1)"""
    if d is None:
        return Eval("NODATA", False, False, False, False, "", "no data", "", None)

    if d.get("stale"):
        return Eval("STALE", False, False, False, False, "", "data stale", "", None)

    age_s = age_seconds(d, now)
    if age_s > STALE_AGE_SECONDS:
        return Eval("STALE", False, False, False, False, "", f"data age {int(age_s)}s > {STALE_AGE_SECONDS}s", "", None)

    if (now.hour == 13 and now.minute >= 15) or (now.hour > 13):
        return Eval("CLOSE", False, False, False, False, "", "after 13:15 no new entry", "", None)

    s = cfg["strategy"]
    f1 = d["p"] > d["vwap"] and d["vd"] > 0

    # #5: BRK confirm 只讀不寫 (在 main 的 update_brk_confirm 已更新)
    brk_confirm_count = cfg["strategy"].get("brk_confirm_bars", 2)
    brk_raw = d["p"] > d["or_h"] and d["vr"] > s["breakout_volume_ratio"]
    f2a = brk_raw and _brk_confirm.get(sym, 0) >= brk_confirm_count

    f2b = abs(d["dp"]) < s["pullback_vwap_range_pct"] and d["vr"] < s["pullback_volume_ratio"]

    f3 = True
    if s.get("index_vwap_required", True):
        if idx is None or idx.get("stale", False) or age_seconds(idx, now) > STALE_AGE_SECONDS:
            f3 = False
        else:
            f3 = idx["p"] > idx["vwap"] and idx["vd"] > 0 and not idx.get("crash", False)

    ext_thr = ext_threshold(d.get("atr_pct"))
    is_ext = d["dp"] > ext_thr

    setup = ""
    risk = None
    detail = ""
    reason = ""

    # #4: Set/R:R 計算只在 f1 通過後才做 (方向不對不顯示 setup)
    if f1 and f2a:
        tick = tick_size(d["or_h"])
        entry = round_to_tick(max(d["or_h"], d["p"]), tick)
        sl = round_to_tick(d["or_l"], tick)
        if entry <= sl:                  # P2: 開盤區間退化 (or_h<=or_l) → 不出 BRK
            setup = ""
            f2a = False
        else:
            setup = "BRK"
            tp = round_to_tick(entry + (entry - sl) * 1.5, tick)   # C1: 對齊 tick
            risk_pct = (entry - sl) / entry * 100
            rr = (tp - entry) / (entry - sl)
            risk = {"entry": entry, "sl": sl, "tp": tp, "risk_pct": risk_pct, "rr": rr, "setup": "BRK"}
            detail = f"BRK Buy>={entry:.0f} SL={sl:.0f} TP={tp:.0f} R:R={rr:.1f} Risk={risk_pct:.1f}%"
            max_rp = s.get("max_risk_pct", 4.0)
            if risk_pct > max_rp:        # 3.1: 停損過寬不出 CALL
                f2a = False
                setup = ""
                risk = None
                detail = ""
                reason = f"risk {risk_pct:.1f}% > max {max_rp}% (停損過寬不追)"
    elif f1 and f2b:
        setup = "PUL"
        entry = d["vwap"]
        tick = tick_size(entry)
        sl = entry - max(tick * 5, entry * 0.01)
        sl = round_to_tick(sl, tick)
        if sl >= entry:
            setup = ""
            f2b = False
        else:
            tp = entry + (entry - sl) * 1.5
            tp = round_to_tick(tp, tick)
            risk_pct = (entry - sl) / entry * 100
            rr = (tp - entry) / (entry - sl)
            risk = {"entry": entry, "sl": sl, "tp": tp, "risk_pct": risk_pct, "rr": rr, "setup": "PUL"}
            detail = f"PUL Buy~{entry:.1f} SL={sl:.1f} TP={tp:.1f} R:R={rr:.1f} Risk={risk_pct:.1f}%"
            max_rp = s.get("max_risk_pct", 4.0)
            if risk_pct > max_rp:        # 3.1: 停損過寬不出 CALL
                f2b = False
                setup = ""
                risk = None
                detail = ""
                reason = f"risk {risk_pct:.1f}% > max {max_rp}% (停損過寬不追)"
    elif f1 and is_ext:
        setup = "EXT"
        reason = f"too extended (dVWAP > {ext_thr:.1f}%)"
    elif f1 and brk_raw and not f2a:
        setup = "cBRK"
        reason = f"BRK confirming ({_brk_confirm.get(sym, 0)}/{brk_confirm_count})"
    elif f1:
        setup = "--"

    if is_ext and f1:
        sg = "PASS"
        reason = "too extended, don't chase"
    elif f1 and (f2a or f2b) and f3:
        sg = "CALL"
        reason = f"{setup} setup, market OK"
    elif f1 and not f3:
        sg = "BLOCK"
        reason = "idx weak/blocking"
    elif f1 and f3:
        sg = "WATCH"
        if not reason:
            reason = "waiting for BRK or PUL trigger"
    else:
        sg = "PASS"
        if not reason:
            if not f1:
                if d["vd"] <= 0: reason = "VWAP falling or flat"
                else: reason = "below VWAP"
            else:
                reason = "conditions not met"

    return Eval(sg, f1, f2a, f2b, f3, detail, reason, setup, risk)

# ── Paper trade log ───────────────────────────────────

_log_path = Path(__file__).parent / "paper_trades.csv"
_logged_calls = set()

def seed_logged_calls(today):
    """啟動/跨日時把當日已寫入 CSV 的 (sym, date) 灌回去重集合 (P3: 防重啟重複寫入)"""
    if not _log_path.exists():
        return
    try:
        with open(_log_path, newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].startswith(today):
                    _logged_calls.add((row[1], today))
    except Exception:
        pass

def log_paper_trade(sg, sym, d, idx, risk, now, cycle):
    if sg != "CALL" or risk is None: return
    key = (sym, now.strftime("%Y-%m-%d"))  # 每股每天只記一次
    if key in _logged_calls: return
    _logged_calls.add(key)

    exists = _log_path.exists()
    with open(_log_path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["time", "symbol", "setup", "entry", "sl", "tp",
                        "rr", "risk_pct", "price", "vwap", "or_h", "or_l",
                        "vol_ratio", "idx_price", "idx_prevC", "chg_pct"])
        w.writerow([
            now.strftime("%Y-%m-%d %H:%M:%S"),
            sym, risk.get("setup", ""),
            f"{risk['entry']:.1f}", f"{risk['sl']:.1f}", f"{risk['tp']:.1f}",
            f"{risk['rr']:.2f}", f"{risk['risk_pct']:.2f}",
            f"{d['p']:.1f}", f"{d['vwap']:.1f}",
            f"{d['or_h']:.1f}", f"{d['or_l']:.1f}",
            f"{d['vr']:.2f}",
            f"{idx['p']:.0f}" if idx else "",
            f"{idx['vwap']:.0f}" if idx else "",
            f"{d['chg_pct']:.2f}",
        ])

# ── 固定欄寬列構建 ────────────────────────────────────

COLS = [
    ("sym",  5, "<"), ("prc",  7, ">"), ("chg",  5, ">"), ("vwp",  7, ">"),
    ("dir",  4, "<"), ("dst",  5, ">"), ("mom",  4, ">"), ("vol",  4, ">"),
    ("set",  4, "<"), ("rr",  10, ">"), ("age",  4, ">"), ("sig",  7, "<"),
]
COL_NAMES = ["Sym", "Price", "Chg%", "VWAP", "Dir", "dVWAP", "Mom", "Vol", "Set", "R:R/Risk", "Age", "Signal"]
COL_WIDTHS = [c[1] for c in COLS]
COL_ALIGNS = [c[2] for c in COLS]
SEP = " "
INNER_W = sum(COL_WIDTHS) + len(SEP) * (len(COLS) - 1) + 1

def make_col(text, width, align):
    s = str(text)
    vw = vis_width(s)
    if vw > width:
        result = ""; rw = 0
        for ch in s:
            cw = 2 if unicodedata.east_asian_width(ch) in ('W','F') else 1
            if rw + cw > width: break
            result += ch; rw += cw
        return result
    if align == ">": return " " * (width - vw) + s
    return s + " " * (width - vw)

def build_row(cells):
    plain = " "
    color_ranges = []
    for i, (txt, color) in enumerate(cells):
        col = make_col(txt, COL_WIDTHS[i], COL_ALIGNS[i])
        start = len(plain); plain += col; end = len(plain)
        if color: color_ranges.append((start, end, color))
        if i < len(cells) - 1: plain += SEP
    for start, end, color in sorted(color_ranges, reverse=True):
        plain = plain[:start] + color(plain[start:end]) + plain[end:]
    return plain

def make_header():
    cells = [(COL_NAMES[i], None) for i in range(len(COLS))]
    return build_row(cells)

# ── 畫面渲染 ──────────────────────────────────────────

def fmt_age(d, now):
    if not d or not d.get("ts"): return "--"
    try:
        t = datetime.strptime(d["ts"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        age = (now - t).total_seconds()
        if age < 60: return f"{int(age)}s"
        return f"{int(age//60)}m"
    except Exception:
        return "?"

def render(results, idx, cfg, ft, cycle, errs):
    os.system("cls" if os.name == "nt" else "clear")
    now = datetime.now(TZ)
    W = INNER_W
    TL, TR, BL, BR = "+", "+", "+", "+"
    HZ, VT, TJ, TK = "-", "|", "+", "+"
    LT = "-"

    print(bo(f"{TL}{HZ*W}{TR}"))
    title = f" TW DayTrade Monitor  {now:%H:%M:%S}  #{cycle} "
    print(bo(VT) + pad_left(title, W) + bo(VT))

    # 大盤行 — #6: 也檢查 age-based stale
    if idx and not idx.get("stale", False) and age_seconds(idx, now) <= STALE_AGE_SECONDS:
        vd_txt = {1: gr("UP"), -1: rd("DN"), 0: di("--")}[idx["vd"]]
        ok = idx["p"] > idx["vwap"] and idx["vd"] > 0 and not idx.get("crash", False)
        tag = gr(" [IDX OK] ") if ok else rd(" [IDX NG] ")
        idx_age = fmt_age(idx, now)
        idx_line = (f"{cy('TWII')} {idx['p']:>8.0f} {vd_txt} {idx['chg_pct']:+.1f}%  "
                    f"PrevC {idx['vwap']:>8.0f}  Age {idx_age:>3}  {tag}")
        if not ok:
            rs = []
            if not (idx["p"] > idx["vwap"]): rs.append("idx<prevC")
            if idx["vd"] <= 0: rs.append("falling")
            if idx.get("crash"): rs.append("crash")
            idx_line += rd(f" ({','.join(rs)})")
        print(bo(VT) + pad_left(idx_line, W) + bo(VT))
    elif idx and idx.get("stale", False):
        print(bo(VT) + pad_left(rd(f" [IDX STALE] data age {int(age_seconds(idx, now))}s, no trades"), W) + bo(VT))
    else:
        print(bo(VT) + pad_left(rd(" [IDX NODATA] index required, no trades"), W) + bo(VT))

    print(bo(f"{TJ}{HZ*W}{TK}"))
    print(bo(VT) + make_header() + bo(VT))
    print(bo(TJ) + di(LT*W) + bo(TK))

    tips = []
    calls_to_log = []   # D2: render 不寫檔,收集 CALL 交給 main 記錄
    for st in cfg["stocks"]:
        sym = st["symbol"]; d = results.get(sym)
        if d is None:
            print(bo(VT) + pad_left(f" {cy(sym):<5} {di('-- no data --')}", W) + bo(VT))
            continue

        sg, f1, f2a, f2b, f3, detail, reason, setup, risk = evaluate(d, idx, cfg, now, sym)
        chg_pct = d["chg_pct"]
        age = fmt_age(d, now)
        is_stale = d.get("stale", False)

        price_color = None
        if d["p"] > d["vwap"] and d["vd"] > 0: price_color = gr
        elif d["p"] < d["vwap"] and d["vd"] < 0: price_color = rd

        chg_color = gr if chg_pct > 0.05 else (rd if chg_pct < -0.05 else di)
        vd_char = {1: ("UP", gr), -1: ("DN", rd), 0: ("--", di)}[d["vd"]]
        dp_color = gr if d["dp"] > 0.05 else (rd if d["dp"] < -0.05 else di)
        mom_color = gr if d["mom"] > 0.05 else (rd if d["mom"] < -0.05 else di)

        vol_tag = " "
        if d["vr"] > 1.5: vol_tag = "H"
        elif d["vr"] < 0.7: vol_tag = "L"
        vol_display = f"{d['vr']:.1f}{vol_tag}"
        vol_color = mg if d["vr"] > 1.5 else (di if d["vr"] < 0.7 else None)

        setup_color = {"BRK": gr, "PUL": yl, "EXT": rd, "cBRK": mg, "--": di, "": di}.get(setup, di)

        if risk:
            rr_display = f"{risk['rr']:.1f}R/{risk['risk_pct']:.1f}%"
            rr_color = gr if risk["risk_pct"] <= 3.0 else (yl if risk["risk_pct"] <= 5.0 else rd)
        else:
            rr_display = "--"
            rr_color = di

        # Age 顏色 (用秒數，不靠字串解析)
        age_s = age_seconds(d, now)
        if is_stale or age_s > STALE_AGE_SECONDS: age_color = rd
        elif age_s > 30: age_color = yl
        else: age_color = di

        sig_map = {
            "CALL": ("[CALL]", gr), "WATCH": ("[WAIT]", yl),
            "BLOCK": ("[BLK] ", mg), "PASS": ("[PASS]", di),
            "STALE": ("[STALE]", rd), "NODATA": ("[NODATA]", di),
            "CLOSE": ("[CLOSE]", yl),
        }
        sig_text, sig_color = sig_map.get(sg, ("[----]", di))

        cells = [
            (sym, cy), (f"{d['p']:.1f}", price_color), (f"{chg_pct:+.1f}%", chg_color),
            (f"{d['vwap']:.1f}", None), (f"{vd_char[0]:>2}", vd_char[1]),
            (f"{d['dp']:+.1f}%", dp_color), (f"{d['mom']:+.1f}%", mom_color),
            (vol_display, vol_color), (setup, setup_color),
            (rr_display, rr_color), (age, age_color), (sig_text, sig_color),
        ]
        print(bo(VT) + build_row(cells) + bo(VT))

        reason_line = f"  Reason: {reason}"
        if is_stale:
            print(bo(VT) + pad_left(rd(f"  Reason: STALE - {reason}"), W) + bo(VT))
        else:
            print(bo(VT) + pad_left(di(reason_line), W) + bo(VT))

        if sg == "CALL":
            tips.append((sg, sym, detail, risk))
            calls_to_log.append((sym, d, risk))   # D2: 交給 main 寫 CSV

    # Action 區
    print(bo(TJ) + di(LT*W) + bo(TK))
    if tips:
        print(bo(VT) + pad_left(f" {bo('Action')}", W) + bo(VT))
        for sg, sym, detail, risk in tips:
            print(bo(VT) + pad_left(f"   {gr('>>')} {cy(sym)} {gr(detail)}", W) + bo(VT))
    else:
        print(bo(VT) + pad_left(f" {di('No entry signal')}", W) + bo(VT))

    print(bo(f"{BL}{HZ*W}{BR}"))

    # 對照表
    mapping = [f"{st['symbol']}={st['name']}" for st in cfg["stocks"]]
    mapping.append("TWII=加權指數")
    print(di("  " + "  ".join(mapping)))

    print(di(f"  信號: {gr('[CALL]')}=進場  {yl('[WAIT]')}=等  {mg('[BLK]')}=大盤擋  {di('[PASS]')}=不作  {rd('[STALE]')}=舊資料  {yl('[CLOSE]')}=過13:15"))
    print(di(f"  Set: BRK突破(需連續2次) PUL拉回 cBRK確認中 EXT過度延伸"))
    print()

    # 狀態列
    ft_s = ft.strftime("%H:%M:%S")
    src = "FinMind(real-time)" if FM_TOKEN else "no token"
    err_parts = []
    if errs > 0: err_parts.append(f"!{errs}")
    if _last_error: err_parts.append(_last_error[:40])
    err_s = f" | {' | '.join(err_parts)}" if err_parts else ""
    print(di(f"  更新 {ft_s} | 週期 {cfg['refresh_seconds']}s | 來源 {src}{err_s}"))
    print()

    return calls_to_log

# ── Config ─────────────────────────────────────────────
def load_cfg():
    for a in sys.argv[1:]:                        # D4: 只接受有效的 config json,壞檔不 crash
        if a.endswith(".json") and Path(a).exists():
            try:
                with open(a) as f: cfg = json.load(f)
            except Exception as e:
                print(f"無法載入 {a}: {e}", file=sys.stderr); continue
            if "stocks" in cfg:
                return cfg
            print(f"忽略 {a}: 缺 stocks 欄位", file=sys.stderr)
    p = Path(__file__).parent / "config.json"
    if p.exists():
        with open(p) as f: return json.load(f)
    return {
        "refresh_seconds": 30,
        "stocks": [{"symbol":"2303","name":"聯電"},{"symbol":"2327","name":"國巨"},
                   {"symbol":"2408","name":"南亞科"},{"symbol":"2344","name":"華邦電"}],
        "index": {"symbol":"001","name":"加權指數"},
        "strategy": {"breakout_volume_ratio":1.5,"pullback_vwap_range_pct":0.5,
                     "pullback_volume_ratio":0.7,"index_vwap_required":True,
                     "brk_confirm_bars":2,"max_risk_pct":4.0}
    }

def market_open():
    n = datetime.now(TZ)
    return n.weekday() < 5 and (9 <= n.hour < 13 or (n.hour == 13 and n.minute <= 30))

# ── Main ───────────────────────────────────────────────
def main():
    global _last_error, _vol_baseline
    cfg = load_cfg(); once = "--once" in sys.argv
    results = {}; idx_data = None; first = True; errs = 0
    idx_sym = cfg["index"]["symbol"]

    # 啟動自我檢查: 真打一次 FinMind, 讓 docker logs 一眼確認 token/連線 (不分盤中盤外)
    probe_sym = cfg["stocks"][0]["symbol"] if cfg.get("stocks") else "2330"
    if not FM_TOKEN:
        print("[watch] startup: 找不到 FINMIND_API token (.env) — 即時資料將失敗", flush=True)
    else:
        _p = fetch_snapshot(probe_sym)
        if _p:
            print(f"[watch] startup: FinMind token OK ({probe_sym} close={_p['p']:.1f} ts={_p['ts']})", flush=True)
        else:
            print(f"[watch] startup: FinMind token/連線 FAIL — {_last_error}", flush=True)

    try:
        cycle = 0
        last_date = None
        while True:
            ft = datetime.now(TZ); cycle += 1
            _last_error = ""; errs = 0          # D5: errs 每 cycle 歸零

            today = ft.strftime("%Y-%m-%d")
            if today != last_date:          # P4: 跨日重置狀態 + P3: 載入當日已記錄
                saved_or = load_opening_range(today)   # 同日重啟還原開盤區間; 跨日為空
                _opening_range.clear(); _opening_range.update(saved_or)
                _prev_snapshots.clear(); _brk_confirm.clear()
                results.clear()
                idx_data = None; first = True
                _logged_calls.clear(); seed_logged_calls(today)
                _vol_baseline = load_baseline(today)
                last_date = today

            if market_open() or first:
                if not _vol_baseline:           # 盤前 baseline.py 較晚才產出 → 盤中補載一次
                    _vol_baseline = load_baseline(today)
                idx_snap = fetch_snapshot(idx_sym)
                if idx_snap:
                    prev_idx = _prev_snapshots.get(idx_sym)
                    idx_data = analyze_index(idx_sym, idx_snap, prev_idx, cfg)
                    idx_data["stale"] = False
                    idx_data["ts"] = idx_snap["ts"]
                    _prev_snapshots[idx_sym] = idx_snap
                    update_opening_range(idx_sym, idx_snap)
                else:
                    errs += 1
                    # #1: 大盤 fetch 失敗 → 標記舊資料 stale
                    if idx_data:
                        idx_data["stale"] = True
                time.sleep(0.2)

                for st in cfg["stocks"]:
                    sym = st["symbol"]
                    snap = fetch_snapshot(sym)
                    if snap:
                        prev = _prev_snapshots.get(sym)
                        update_opening_range(sym, snap)   # P5: 先更新 OR 再分析,首 cycle 不退化成當日高低
                        d = analyze_stock(sym, snap, prev, cfg)
                        d["stale"] = False
                        d["ts"] = snap["ts"]
                        results[sym] = d
                        _prev_snapshots[sym] = snap
                        # #5: BRK confirm 在資料更新階段做，不在 render
                        update_brk_confirm(sym, d, cfg)
                    else:
                        errs += 1
                        if sym in results and results[sym]:
                            results[sym]["stale"] = True
                        _brk_confirm[sym] = 0  # #3: fetch 失敗時重置 BRK 確認
                    time.sleep(0.2)

                first = False
                save_opening_range(today)   # 個股 OR 更新完後持久化,供同日重啟還原

            calls = render(results, idx_data, cfg, ft, cycle, errs)
            for csym, cd, crisk in calls:          # D2: 寫檔移出 render
                log_paper_trade("CALL", csym, cd, idx_data, crisk, ft, cycle)
            if once: break
            time.sleep(cfg.get("refresh_seconds", 30))
    except KeyboardInterrupt:
        print(f"\n  {di('已停止')}")

if __name__ == "__main__":
    main()
