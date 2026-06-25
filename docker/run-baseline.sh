#!/bin/sh
# 盤前每日刷新量能/ATR 基準 (avg20_vol.json)。
# 啟動先跑一次,之後每天 08:30 (Asia/Taipei) 再刷新。
# baseline.py 失敗不致命: watch.py 會 fallback 用 FinMind volume_ratio。
while true; do
  python3 baseline.py || echo "[baseline] 失敗,沿用舊 avg20_vol.json"
  now=$(date +%s)
  next=$(date -d "08:30 today" +%s)
  [ "$now" -ge "$next" ] && next=$(date -d "08:30 tomorrow" +%s)
  echo "[baseline] 下次刷新 $(date -d "@$next" '+%F %T'),睡 $((next - now))s"
  sleep "$((next - now))"
done
