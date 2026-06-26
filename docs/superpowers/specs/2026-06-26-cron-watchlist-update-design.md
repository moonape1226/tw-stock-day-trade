# 設計：每交易日盤後自動更新 watchlist (cron)

## 目標

每個交易日盤後自動用 `screen_v2.py` 選股，覆寫 `config.json` 的 `stocks`，並重啟 `watch` / `track` 服務，讓隔日盯盤清單免人工維護。

## 背景與約束

- 部署在遠端 `infra-test` (`carl@infra-test`)，專案路徑 `/home/carl/tw-stock-day-trade`，是本 repo 的 git clone。
- 主機時區 = UTC。台股收盤 13:30 TW = 05:30 UTC。
- 三服務以 docker compose 常駐，`.:/app` bind-mount，產出檔在 host。
- `watch.py` 只在啟動時 `load_cfg()` 一次（`watch.py:718`，迴圈內不重讀）→ 改 config 必須重啟 watch 才生效。
- `baseline.py` 每天 08:30 TW 讀 `config.json` 算 `avg20_vol.json`（`baseline.py:36-37`）→ config 必須在 08:30 前就位。
- `screen_v2.py --json` 用 TWSE openapi + MIS（免 token），輸出已排序（A→B→C，去除 D）的候選 JSON 陣列，每筆含 `code` / `name` / `bucket` / `score`。

## 決策（已與使用者確認）

- 自動化程度：**全自動**（選股 → 備份 → 覆寫 config → restart）。
- 標的數：**Top 5，bucket A 優先、不足用 B 補**。
- 執行時間：**15:30 TW = 07:30 UTC，週一至五**。
- Guard floor：選到 **< 3 檔即中止**，保留原 config、不重啟。

## 架構

主機 cron + 兩支腳本。screen 借容器環境跑（不必在 host 裝 python deps），restart 由 host 執行（容器內不便重啟 sibling 服務）。

### 元件 1：`update_watchlist.py`（repo，於容器內執行）

職責：產生新 watchlist 並寫回 `config.json`。

- 以 subprocess 呼叫 `python3 screen_v2.py --json`，解析候選陣列（沿用既有公開介面，**不修改 screen_v2.py**）。
- 依序取 `bucket` 為 `A`、再 `B` 的候選，合計取前 5 檔（輸入已按 bucket→score 排序）。
- **Guard**：若選到 < 3 檔 → 印錯誤、**不寫檔**、退出碼 `2`。
- 寫檔前把現有 `config.json` 複製到 `config.json.bak`（單份滾動備份）。
- 只替換 `stocks` 鍵為 `[{"symbol": code, "name": name}, ...]`，保留 `index` 與 `strategy` 原值。
- 以 `indent=2, ensure_ascii=False` 寫回 `config.json`。
- 印一行摘要（日期、選到幾檔、代碼清單）。
- 退出碼：`0` = 有變更、`2` = 中止/未變更。
- `--dry-run`：執行選股與組裝但不備份、不寫檔，只印擬議 `stocks`；退出碼 `0`。

依賴：`screen_v2.py`（同目錄）、`config.json`、容器內 python3。

### 元件 2：`cron-update-watchlist.sh`（repo，於 host 執行）

職責：串接「跑 update_watchlist → 視結果重啟服務」，並記錄。

- `cd /home/carl/tw-stock-day-trade`。
- 印時間戳起始行。
- `docker compose run --rm --no-deps watch python3 update_watchlist.py`，擷取退出碼。`run` 帶 bind-mount，容器內寫的 `config.json` 直接落地 host。
- 退出碼 `0` → `docker compose restart watch track`，記「已套用並重啟」。
- 其他退出碼 → 記「未變更/中止」，不重啟。
- 全程 stdout/stderr 由 crontab 重導附加到 `watchlist-cron.log`。

依賴：host 的 `docker compose`、元件 1。

### 元件 3：crontab 項目

```
30 7 * * 1-5  /home/carl/tw-stock-day-trade/cron-update-watchlist.sh >> /home/carl/tw-stock-day-trade/watchlist-cron.log 2>&1
```

07:30 UTC = 15:30 TW，週一至五。

## 資料流

1. cron 於 07:30 UTC 觸發 wrapper。
2. wrapper 起一次性容器跑 `update_watchlist.py`。
3. screen_v2 抓 TWSE/MIS → 候選 JSON。
4. update_watchlist 取 Top 5 (A→B)，guard 通過則備份 + 覆寫 host 的 `config.json`，退出碼 0。
5. wrapper 見 0 → `docker compose restart watch track`。此時已收盤，watch 重啟即 idle。
6. 隔日 08:30 TW baseline 讀新 config 算 avg20；09:00 開盤對齊。

## 時序安全

15:30 TW 重啟時 `market_open()` 為 false，watch 直接 idle，不影響任何盤中狀態（開盤區間、持倉 json 當日已無用）。baseline 不被重啟、持續其 08:30 排程。

## 錯誤處理

| 情況 | 行為 |
|------|------|
| screen_v2 失敗 / 回空 | update_watchlist 選到 < 3 檔 → 退出碼 2 → 不覆寫、不重啟、保留舊 config |
| config.json 解析失敗 | update_watchlist 例外退出（非 0）→ wrapper 不重啟 |
| 寫檔前已有 `config.json.bak` | 直接覆蓋（單份滾動備份） |
| restart 失敗 | docker compose 回非 0，記錄於 log；服務維持舊狀態（`restart: unless-stopped`） |

## 已知限制

平日國定假日（週間休市）：screen_v2 會回上一交易日的舊資料，update_watchlist 仍會覆寫成「昨日名單」。影響小（名單多半雷同），先不處理；日後可在 update_watchlist 加 `get_market_holiday_schedule` 假日 guard，假日直接退出碼 2。

## 測試

- `update_watchlist.py` 的純函式（從候選陣列挑 Top N、A→B 補齊、floor guard、合併進 config 保留 index/strategy）以 unittest 覆蓋，餵假候選資料，不打網路。
- 部署前在遠端跑 `docker compose run --rm --no-deps watch python3 update_watchlist.py --dry-run` 驗證實際選到 5 檔且格式正確。

## 部署

1. 兩支腳本 commit 進 repo、push。
2. 遠端 `git pull`。
3. `chmod +x cron-update-watchlist.sh`。
4. 安裝 crontab 項目。
5. `--dry-run` 驗證一次。
