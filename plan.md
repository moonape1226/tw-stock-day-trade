# 台股「選股+當沖」策略修改計劃

> 第一性原理 review 後產出。findings 已逐項對照 `screen.py` / `screen_v2.py` / `watch.py` / `chips.py` / `config.json` 原始碼驗證（26/37 確認）。
> 既有分析見 `first_principal_codex.md`、`first_principal_opencode.md`；本計劃為其驗證 + 補遺 + 排序。

## 1. 總體判斷

架構是對的（OR 突破 + VWAP 拉回 + 大盤濾網 + paper log），但**現在無法證明自己賺錢**，且數個量能/方向訊號因「拿累計型數值對固定門檻比較」在盤中系統性失效。

- **站得住**：選股骨架（流動性 gate、ATR%、RVOL 概念）與訊號分層（A/B/C/D、BRK/PUL）方向正確。
- **站不住**：(a) 沒有出場/P&L 閉環，所有門檻都是盲調；(b) 三個核心訊號資料語意錯誤——`volume_ratio` 被當盤中 RVOL、`vd` 用累計 VWAP 30s 差分午後恆為 0、screen 的 RVOL 是時段時鐘。

**第一優先：先建出場追蹤+回測閉環，再修這三個資料 bug。閉環不存在前，任何門檻調整都是猜的。**

## 實作狀態（已完成）

| 項目 | 狀態 | 落點 |
|---|---|---|
| 1.1/1.2/1.4 出場閉環 + 淨成本 R + 重啟韌性 | ✅ | `track.py`（純邏輯 17 測試）|
| 1.3 BRK entry 用確認當下實價 max(or_h, p) | ✅ | `watch.py` evaluate() |
| 2.1 盤中 RVOL（盤前基準）| ✅ | `baseline.py`→`avg20_vol.json`→`watch.py`；**單位 bug 已修**（total_volume=張，不 /1000）|
| 2.2 vd 改 VWAP 位置+價格動能（修午後死訊號）| ✅ | `watch.py` stock_vwap_dir（7 測試）|
| 2.3 screen RVOL 時段正規化（U 形曲線）| ✅ | `screen_v2.py` expected_vol_fraction（2 測試）|
| 2.4 當月快取過期 | ✅ | `screen_v2.py` stock_day |
| 3.1 停損過寬不出 CALL（max_risk_pct=4%）| ✅ | `watch.py` + `config.json` |
| 3.4 EXT 隨 ATR 適配（k×atr_pct, 夾[1,4]）| ✅ | `baseline.py` 匯出 atr20 + `watch.py` ext_threshold（4 測試）|
| 開盤區間重啟持久化（server 殘留風險）| ✅ | `watch.py` opening_range.json |
| 3.2 開盤區間用 1 分 K 重建 | ⏭️ 略過 | server 常駐 9:00 前開盤，不會晚開→無意義 |
| 3.5 大盤 vd/crash 平滑 | ⏸️ 延後 | 邊際；待 server 有 net R 再評估 |
| §4 做空 / sector RS / 法人整合 / 兩段式出場 | ⏸️ 延後 | 先證明多方有正期望值 |

測試：`python3 test_track.py`(17) + `test_watch.py`(16) + `test_screen.py`(2)，共 35 全綠。

## 部署順序（server）

1. **盤前（開盤前任意時間）**：`python3 baseline.py` → 產生 `avg20_vol.json`（各股 avg20 量 + atr20）。
2. **開盤前啟動、整天常駐**：`python3 watch.py` → 進場訊號面板 + `paper_trades.csv`。務必 9:00 前啟動（開盤區間正確），中途 crash 由 `opening_range.json` 還原。
3. **同時常駐**：`python3 track.py` → 讀 `paper_trades.csv` 追蹤出場，寫 `trades_closed.csv`（gross R / net R 扣 0.45%）。
4. 累積 ≥15 交易日後，看 `trades_closed.csv` 算 BRK/PUL 各自 net R 與勝率，再決定 3.5/§4 值不值得做。

> 已知待 server 實測校正：(a) `INTRADAY_VOL_CURVE` U 形曲線是近似值；(b) 2316/6271 抓不到 TWSE 日線基準（可能上櫃），會 fallback FinMind volume_ratio。

## 2. 修改計劃（依優先序）

### Phase 1 — 先讓 edge 可被量測

| # | 改什麼 | 第一性理由 | 量 | 相依 |
|---|--------|-----------|----|------|
| 1.1 | **出場追蹤閉環**：`watch.py` main loop 維護 `open_positions`（sym→entry/sl/tp/setup/ts），每 cycle 用 snap high/low 判 TP（high≥tp）/SL（low≤sl），13:15 強制以現價平倉，命中寫 `trades_closed.csv`（entry, exit, exit_reason, R_realized, net_pnl_pct）。純加法，不動進場邏輯。 | 原則5：沒閉環就無法證偽 | M | **最先做** |
| 1.2 | **淨成本納入 R**：出場 R 扣 0.45% 來回成本，paper log 同記 net R 與 gross R。 | 原則2：1.5R 名目=40% breakeven，扣成本真實 hurdle ≈56% | S | 1.1 |
| 1.3 | **BRK entry 用確認當下實價**：`watch.py:281` entry 改記 `max(or_h, d["p"])`（或 `d["p"]`），反映 ≥60s 確認延遲的追價成本；CSV 已有 `price` 欄可交叉校正。 | 原則4：entry 是 R 分母，偏低使 BRK 虛高 | S | 1.1 |
| 1.4 | **重啟韌性（server 無人值守必要）**：`open_positions` 及開盤區間 `_opening_range` 持久化到 disk（小 json），main loop 啟動時讀回繼續追蹤。對照現有 `seed_logged_calls`（`watch.py:347`）的做法。 | 原則5：server 半路重啟（crash/部署/OOM）若狀態只在記憶體，當天 BRK 與出場追蹤靜默斷裂，樣本出現不可控缺漏 | S | 1.1 |

> 完成後跑 N 天 paper，才第一次能算出 BRK/PUL 各自淨 R 與勝率。這是後續所有調參前提。
> 部署模式：跑在專用 server，迴圈長時間掛著（非開盤時段 idle、跨日由 `last_date` 自動重置），靠 1.4 撐過重啟。

### Phase 2 — 修「決定訊號是否觸發」的資料語意 bug

| # | 改什麼 | 第一性理由 | 量 | 相依 |
|---|--------|-----------|----|------|
| 2.1 | **`volume_ratio` 不可當盤中量能**（`watch.py:76/259/262/498`）：FinMind `volume_ratio` 是昨/今全日量比，有強烈 time-of-day 漂移。改成自維護「本 cycle 增量量 = total_vol(t) − total_vol(t-1)」對同時段歷史均量正規化的 time-of-day RVOL。驗證前 BRK/PUL 量能條件視為未實作。 | 原則2/3：量能定義錯=沒有量能確認，BRK 早盤全過、PUL 早盤永不觸發 | M | Phase 1 |
| 2.2 | **`vd` 改用價格相對 VWAP 斜率**（`watch.py:134-139`）：累計 VWAP 30s 差分午後 <0.02%，vd 恆 0，`f1` 整個下午失效且 backtest 不可重現。改用 price 對 VWAP 位置 + 距離變化（dp 變化），或對變動量做時段正規化。 | 原則3/4：方向濾網半天結構性失效=半天沒訊號 | M | Phase 1；與 2.1 同源同批改 |
| 2.3 | **screen RVOL 時段正規化**（`screen_v2.py:164`）：`rvol = v / (avg20_vol × expected_frac(now))`，用 20 日盤中累計量曲線或固定 U 形表。修好前文件標註 screen_v2 RVOL 只在收盤後有效。 | 原則3/5：RVOL 是 A 桶最大權重，盤中誤觸發使排名偏誤 | M | 與 2.1/2.2 同類 |
| 2.4 | **快取新鮮度**（`screen_v2.py:68-72`）：當月檔不可無條件當 final 快取，存 as-of 日或跳過 `this_ym` 快取，過期即失效；`prev_ym` 可永久快取。 | 原則5：avg20/ATR 視窗每天靜默倒退一天且不報錯 | S | 無 |

### Phase 3 — 風險約束與環境濾網（閉環有數據支撐後再調）

| # | 改什麼 | 第一性理由 | 量 |
|---|--------|-----------|----|
| 3.1 | **max_risk_pct gate**（`watch.py:283-292`）：risk_pct 超限（如 3-4%）降級 WATCH 或改近結構停損（VWAP/OR 中點），不無條件出單（紅字只是視覺，擋不住寫 log 與 Action）。 | 原則2/5：一筆 7% 停損抹掉 ~1.5 筆獲利 | S |
| 3.2 | **opening range 用資料時間戳重建**（`watch.py:103-117`）：9:30 後啟動會用啟動瞬間全日 high/low 凍結，BRK 退化成「啟動後新高」。改用 1 分 K 重建 9:00-9:30，或過 9:30 啟動標 BRK=N/A 不出 CALL/不寫 log。 | 原則5：同規則不同啟動時間=不同 or_h/or_l，信號不可重現 | M |
| 3.3 | **大盤濾網語意正名**（`watch.py:170`）：index `vwap` 實為昨收，變數/UI/config key 一律正名 prevClose，並考慮自算指數當日 VWAP（成分股加權或日內均價）。 | 原則3：唯一 regime 濾網，基準比錯會放行整段走弱 | S-M |
| 3.4 | **EXT 門檻隨 ATR 適配**（`watch.py:201/271`）：固定 2% 改 `k × atr_pct`（由 screen_v2 帶入 ATR%）。 | 原則2/4：過度延伸應隨個股波動，同尺對所有股錯誤抑制/放行 | S |
| 3.5 | **大盤 vd/crash 平滑**（`watch.py:172-184`）：±0.01% 噪音即翻向、crash 窗口耦合 refresh_seconds、ts 未前進算出假 0。改多 cycle 斜率 + ts 前進檢查，crash 改固定時間窗。 | 原則3：強多日 30s 微回檔誤 BLOCK，系統性 false-negative | S |

## 3. 新發現（兩份文件遺漏，本次加值）

多為實作/資料 bug，**直接決定訊號是否觸發**，優先級高於策略微調：

1. **`volratio-misdefinition`（high，資料）** — `volume_ratio` 昨/今全日量比被當盤中 RVOL。BRK 1.5、PUL 0.7 全部對著時段漂移、語意不明的數字在比。**Phase 2 最重要單一修正。**
2. **`vd-cumvwap-dead-signal` / `vd-threshold-dead-afternoon`（high，資料/實作）** — 累計 VWAP 30s 差分午後恆 0，`f1` 整個下午結構性失效，backtest 不可重現（依賴 live snapshot 間隔）。
3. **`this-month-cache-staleness`（high，實作）** — 當月快取無新鮮度檢查，avg20/ATR 視窗每天靜默倒退一天，不報錯。工作量 S，CP 值最高。
4. **`crash-window-coupled-to-refresh` / `index-vd-flips-on-noise`（medium，實作）** — 大盤動能耦合 refresh_seconds、首 cycle 永不 crash、ts 未前進算出假 0。
5. **`ext-threshold-hardcoded-vs-dp`（medium，策略）** — EXT 2% 寫死不隨 ATR 適配。
6. **`prev-month-fetch-silent-drop`（low，實作）** — 月初 prev_ym 抓取失敗（網路 blip）使合格股靜默掉出，空 [] 還被寫進快取持續污染。至少要 log 被丟掉的代碼。
7. **`rvol-score-saturation` / `bucket-b-no-rvol-gate` / `inconsistent-liquidity-gates`（low）** — 選股排名/分桶次級瑕疵，Phase 3 後順手處理。

## 4. 明確不要做（避免過度工程）

在 Phase 1 閉環就緒、能算出真實淨 R 之前，以下一律不做：

- **不做空方分支**：long-only 是 `screen_v2.py:17` 刻意 scope，非 bug；且該 finding「空頭日整天 BLOCK」機制在程式上是錯的（弱股走 PASS 非 BLOCK）。空方要等多方先被證明有正期望值。
- **不做 sector RS / leader-laggard / 同族群同步**：5 檔 watchlist、每族群至多 2 檔，統計上資料太薄，且 finding 自承無回測前無法證明邊際效益。
- **不把 chips 法人方向接進盤中決策**：盤後資料不可當盤中觸發；最多盤前過濾 watchlist（低成本）。
- **不為兩段式出場（賣半+移動停利）建複雜模型**：Phase 1 先用單一全倉 1.5R 這一種可量測出場模型當 baseline，等有數據再比兩段式。
- **不先動 `rvol-score-saturation` transform**：RVOL 值本身（Phase 2 前）還是失真的，先正規化再談飽和曲線。
- **被高估、應降級的 finding**：`index-vwap-is-prevclose`（high 那版）宣稱「開高走低整天放行」被誇大——f3 是三元 AND，vd/crash 已逐 cycle 擋下走低，真正漏洞只是慢速陰跌窄帶，應降為 low，排 Phase 3。

## 5. 驗收標準

**Phase 1（閉環）有效 ⟺**
- 跑 ≥15 交易日 paper 後，`trades_closed.csv` 能獨立算出 BRK 與 PUL 各自的：樣本數、勝率、平均 gross R、**平均 net R（扣 0.45%）**、最大連虧。
- 每筆 CALL 都有對應 close（無懸空進場），13:15 後無新進場且未平倉部位被強制平倉。
- BRK 的 entry 與 CSV `price` 欄差距可量化（確認 entry 偏樂觀幅度已被修正）。

**Phase 2（資料 bug）有效 ⟺**
- 修 `vd` 後，CALL 數量時段分佈不再午後歸零（對比修正前後每小時 CALL 數）。
- 修 `volume_ratio` 後，BRK 不再早盤全過、PUL 不再午後全過（時段觸發率趨平）。
- 修快取後，連兩日同月跑 screen，avg20_vol 不再每天倒退。

**整體底線**：net R 已扣成本前提下，至少一種 setup（BRK 或 PUL）的 net 期望值為正且樣本 ≥30 筆。若兩種都為負，停止加濾網，回頭檢查訊號定義。

---

**一句話**：先做「能算帳」（Phase 1）→ 再修「算出來的帳是用錯誤訊號產生的」三個資料 bug（Phase 2）→ 最後才談風險上限與環境濾網（Phase 3）。空方、sector RS、法人、兩段式出場全部延後到多方被證明有正期望值之後。
