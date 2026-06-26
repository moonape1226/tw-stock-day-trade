#!/usr/bin/env python3
"""
盤後自動更新 watchlist — 跑 screen_v2 選股,覆寫 config.json 的 stocks。

於容器內由 cron-update-watchlist.sh 觸發。流程:
  1. subprocess 跑 `screen_v2.py --json` 取候選 (已排序 A→B→C, 去除 D)
  2. 取 bucket A 優先、不足用 B 補, 共 TARGET 檔
  3. 選到 < FLOOR 檔 → 中止 (退出碼 2), 不寫檔、保留舊 config
  4. 備份 config.json → config.json.bak, 只換 stocks (保留 index/strategy)

用法:
  python3 update_watchlist.py            # 選股並覆寫 config.json
  python3 update_watchlist.py --dry-run  # 只印擬議 stocks, 不寫檔
退出碼: 0=有變更(或 dry-run), 2=候選不足、未變更。
"""
import json, subprocess, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CONFIG = SCRIPT_DIR / "config.json"
BACKUP = SCRIPT_DIR / "config.json.bak"
SCREEN = SCRIPT_DIR / "screen_v2.py"

TARGET = 5   # 目標檔數
FLOOR = 3    # 少於此數即中止


def select_stocks(candidates, target=TARGET):
    """從 screen 候選取 bucket A 優先、再 B, 共 target 檔。回傳 [{symbol, name}]。"""
    picked = []
    for bucket in ("A", "B"):
        for c in candidates:
            if c.get("bucket") == bucket:
                picked.append({"symbol": c["code"], "name": c["name"]})
                if len(picked) >= target:
                    return picked
    return picked


def build_new_config(cfg, stocks):
    """回傳新 config dict: 換掉 stocks, 其餘鍵 (index/strategy/...) 原樣保留。不改動輸入。"""
    new = dict(cfg)
    new["stocks"] = stocks
    return new


def run_screen():
    out = subprocess.run(
        [sys.executable, str(SCREEN), "--json"],
        cwd=str(SCRIPT_DIR), capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write(f"[update_watchlist] screen_v2 失敗 (rc={out.returncode}): {out.stderr[-300:]}\n")
        sys.exit(2)
    return json.loads(out.stdout)


def main():
    dry = "--dry-run" in sys.argv[1:]

    candidates = run_screen()
    stocks = select_stocks(candidates)
    if len(stocks) < FLOOR:
        sys.stderr.write(f"[update_watchlist] 候選僅 {len(stocks)} 檔 (< {FLOOR}) → 中止, 保留舊 config\n")
        sys.exit(2)

    cfg = json.loads(CONFIG.read_text())
    new_cfg = build_new_config(cfg, stocks)
    codes = ", ".join(s["symbol"] for s in stocks)

    if dry:
        print(f"[update_watchlist] dry-run: 擬議 {len(stocks)} 檔 → {codes}")
        print(json.dumps(stocks, ensure_ascii=False, indent=2))
        return

    BACKUP.write_text(CONFIG.read_text())
    CONFIG.write_text(json.dumps(new_cfg, ensure_ascii=False, indent=2) + "\n")
    print(f"[update_watchlist] 已更新 config.json: {len(stocks)} 檔 → {codes} (備份 {BACKUP.name})")


if __name__ == "__main__":
    main()
