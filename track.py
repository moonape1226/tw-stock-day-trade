#!/usr/bin/env python3
"""
台股當沖出場追蹤 / paper P&L 閉環 (Phase 1)

獨立於 watch.py 面板。讀 watch.py 產生的 paper_trades.csv 進場訊號，
盤中自行 poll FinMind snapshot，對每筆未平倉部位判 TP/SL、13:15 強制平倉，
把實現損益 (gross R / net R / net%) 寫入 trades_closed.csv。

出場模型: 單一全倉 1.5R baseline (觸 TP 全出 / 觸 SL 全出 / 13:15 收盤)。
判定顆粒度: 每 refresh 用「當下成交價」對 tp/sl 比，先觸先平；
            poll 與 poll 之間的影線不捕捉 (snapshot 模擬的固有誤差，偏保守)。
重啟韌性: 未平倉部位持久化於 open_positions.json，啟動讀回續追；跨日捨棄。

用法:
  python3 track.py            # 盤中持續追蹤 (預設 30s)
  python3 track.py --once     # 跑一次 (煙霧測試)
"""

import json, urllib.request, time, sys, csv, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))
CUTOFF_H, CUTOFF_M = 13, 15

CLOSED_HEADER = ["close_time", "symbol", "setup", "entry", "sl", "tp",
                 "exit_price", "exit_reason", "gross_r", "net_r",
                 "net_pnl_pct", "hold_secs"]


def check_exit(sl, tp, price, now):
    """單一全倉做多出場判定 (poll 顆粒度)。
    優先序: SL > TP > 13:15 收盤。回傳 (reason, exit_price) 或 None。
    SL/TP 假設以掛單價成交; 同一 poll 只可能觸發其一 (sl<entry<tp)。"""
    if price <= sl:
        return ("SL", sl)
    if price >= tp:
        return ("TP", tp)
    if now.hour > CUTOFF_H or (now.hour == CUTOFF_H and now.minute >= CUTOFF_M):
        return ("CLOSE", price)
    return None


def parse_new_entries(rows, today, seen):
    """從 paper_trades.csv 列 (dict) 取出今日、尚未追蹤/平倉的新進場。
    seen: 今日已在追或已平倉的 symbol 集合 (不就地修改)。"""
    out = []
    picked = set(seen)
    for r in rows:
        if not r.get("time", "").startswith(today):
            continue
        sym = r["symbol"]
        if sym in picked:
            continue
        picked.add(sym)
        out.append({
            "symbol": sym, "setup": r["setup"],
            "entry": float(r["entry"]), "sl": float(r["sl"]), "tp": float(r["tp"]),
            "entry_ts": r["time"],
        })
    return out


def realized_r(entry, exit_price, sl, cost_pct):
    """做多單一全倉的實現 R。risk = entry - sl。
    net 扣來回成本 (cost_pct% of entry)。"""
    risk = entry - sl
    cost = entry * cost_pct / 100
    return {
        "gross_r": (exit_price - entry) / risk,
        "net_r": (exit_price - entry - cost) / risk,
        "net_pnl_pct": (exit_price - entry) / entry * 100 - cost_pct,
    }


def build_closed_row(pos, reason, exit_price, now, cost_pct):
    """組 trades_closed.csv 一列 (對齊 CLOSED_HEADER)。"""
    r = realized_r(pos["entry"], exit_price, pos["sl"], cost_pct)
    try:
        opened = datetime.strptime(pos["entry_ts"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        hold = int(max(0, (now - opened).total_seconds()))
    except Exception:
        hold = ""
    return [
        now.strftime("%Y-%m-%d %H:%M:%S"), pos["symbol"], pos["setup"],
        f"{pos['entry']:.2f}", f"{pos['sl']:.2f}", f"{pos['tp']:.2f}",
        f"{exit_price:.2f}", reason,
        f"{r['gross_r']:.3f}", f"{r['net_r']:.3f}", f"{r['net_pnl_pct']:.2f}", hold,
    ]


# ── I/O 外殼 (非純函數; 核心邏輯已於 test_track.py 覆蓋) ──────────────

DIR = Path(__file__).parent
PAPER_LOG = DIR / "paper_trades.csv"      # 輸入: watch.py 進場訊號
CLOSED_LOG = DIR / "trades_closed.csv"    # 輸出: 出場/實現損益
STATE = DIR / "open_positions.json"       # 重啟韌性
COST_PCT = 0.45
FM_SNAP = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"

_last_error = ""


def load_env():
    env = {}
    p = DIR / ".env"
    if p.exists():
        with open(p) as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env


FM_TOKEN = load_env().get("FINMIND_API", "") or load_env().get("FINDMIND_API", "")


def load_refresh():
    p = DIR / "config.json"
    if p.exists():
        try:
            with open(p) as f:
                return int(json.load(f).get("refresh_seconds", 30))
        except Exception:
            pass
    return 30


def fetch_price(sym):
    """回傳 {'p': 最新成交價, 'ts': 時間} 或 None。"""
    global _last_error
    try:
        req = urllib.request.Request(f"{FM_SNAP}?data_id={sym}",
                                     headers={"Authorization": f"Bearer {FM_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read()).get("data", [])
        if not rows:
            _last_error = f"{sym}: empty"
            return None
        return {"p": float(rows[0].get("close", 0)), "ts": rows[0].get("date", "")}
    except Exception as e:
        _last_error = f"{sym}: {e}"
        return None


def read_paper_rows():
    if not PAPER_LOG.exists():
        return []
    try:
        with open(PAPER_LOG, newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def closed_syms_today(today):
    """今日已平倉的 symbol (避免 paper log 同一檔重複進追蹤)。"""
    out = set()
    if not CLOSED_LOG.exists():
        return out
    try:
        with open(CLOSED_LOG, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("close_time", "").startswith(today):
                    out.add(row["symbol"])
    except Exception:
        pass
    return out


def load_state(today):
    """讀回未平倉部位; 僅當持久化日期 == today 才續用，否則捨棄 (跨日)。"""
    if not STATE.exists():
        return {}
    try:
        d = json.loads(STATE.read_text())
    except Exception:
        return {}
    if d.get("date") == today:
        return d.get("positions", {})
    leftover = d.get("positions", {})
    if leftover:
        print(f"# 捨棄前一日 {d.get('date')} 未平倉 {len(leftover)} 筆 "
              f"(process 曾在 13:15 平倉前中斷): {', '.join(leftover)}", file=sys.stderr)
    return {}


def save_state(positions, today):
    try:
        STATE.write_text(json.dumps({"date": today, "positions": positions}, ensure_ascii=False))
    except Exception as e:
        print(f"# 狀態寫入失敗: {e}", file=sys.stderr)


def write_closed(pos, reason, exit_price, now):
    exists = CLOSED_LOG.exists()
    with open(CLOSED_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(CLOSED_HEADER)
        w.writerow(build_closed_row(pos, reason, exit_price, now, COST_PCT))


def market_open(now):
    return now.weekday() < 5 and (9 <= now.hour < 13 or (now.hour == 13 and now.minute <= 30))


def main():
    once = "--once" in sys.argv
    refresh = load_refresh()
    last_date = None
    positions, seen = {}, set()

    # 啟動自我檢查: 真打一次 FinMind, 讓 docker logs 一眼確認 token/連線
    if not FM_TOKEN:
        print("[track] startup: 找不到 FINMIND_API token (.env) — 出場追蹤將失敗", flush=True)
    else:
        _p = fetch_price("2330")
        if _p:
            print(f"[track] startup: FinMind token OK (2330 close={_p['p']:.1f} ts={_p['ts']})", flush=True)
        else:
            print(f"[track] startup: FinMind token/連線 FAIL — {_last_error}", flush=True)

    try:
        while True:
            now = datetime.now(TZ)
            today = now.strftime("%Y-%m-%d")
            if today != last_date:                      # 跨日: 讀回當日狀態 + 已平倉集合
                positions = load_state(today)
                seen = set(positions) | closed_syms_today(today)
                last_date = today

            # 收盤後若仍有未平倉部位 (process 剛重啟)，也要繼續跑到把它們 13:15 平掉
            if market_open(now) or once or positions:
                new = parse_new_entries(read_paper_rows(), today, seen)
                for p in new:
                    positions[p["symbol"]] = p
                    seen.add(p["symbol"])

                for sym in list(positions.keys()):
                    snap = fetch_price(sym)
                    if not snap:
                        continue
                    pos = positions[sym]
                    ev = check_exit(pos["sl"], pos["tp"], snap["p"], now)
                    if ev:
                        reason, xprice = ev
                        write_closed(pos, reason, xprice, now)
                        del positions[sym]
                    time.sleep(0.2)

                save_state(positions, today)
                closed_today = len(seen) - len(positions)
                err = f" | {_last_error[:40]}" if _last_error else ""
                print(f"{now:%H:%M:%S} 追蹤 {len(positions)} 筆 | 本輪新增 {len(new)} | "
                      f"今日已平 {closed_today}{err}")

            if once:
                break
            time.sleep(refresh)
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
