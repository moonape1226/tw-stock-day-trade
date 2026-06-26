#!/bin/sh
# 盤後自動更新 watchlist (cron 觸發, 主機端)。
#   1. 起一次性容器跑 update_watchlist.py (bind-mount → 寫回 host config.json)
#   2. 退出碼 0 (有變更) 才 restart watch/track 讓新 config 生效
# 由 crontab 重導附加到 watchlist-cron.log。15:30 TW 已收盤, 重啟 watch 即 idle。
set -u
cd "$(dirname "$0")" || exit 1

echo "===== $(date -u '+%F %T %Z') update-watchlist 開始 ====="

docker compose run --rm --no-deps watch python3 update_watchlist.py
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "[cron] config 已更新, 重啟 watch/track"
  docker compose restart watch track
else
  echo "[cron] update_watchlist 退出碼 $rc → 未變更, 不重啟"
fi
echo "===== $(date -u '+%F %T %Z') 結束 (rc=$rc) ====="
