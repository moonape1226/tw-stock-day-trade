# 台股當沖監控面板 — 操作手冊

## 安裝與啟動

### 需求

- Python 3.10+
- FinMind Sponsor 帳號（即時資料需要）
- macOS / Linux（Windows 需自行替換 `cls`）

### 檔案結構

```
tw-stock-day-trade/
├── watch.py            # 監控面板主程式
├── config.json         # 設定檔（股票清單、策略參數）
├── .env                # FinMind API Token（不入 git）
├── .gitignore          # 排除 .env / 狀態檔
└── paper_trades.csv    # 自動產生的模擬交易記錄（每次 CALL 一列）
```

### 設定 .env

```
FINMIND_API="你的 FinMind token"
```

### 啟動

```bash
# 持續監控（每 30 秒更新）
python3 watch.py

# 只跑一次看結果
python3 watch.py --once

# 用自訂 config
python3 watch.py my_config.json

# 停止
Ctrl+C
```

---

## 面板說明

### 畫面範例

```
+---------------------------------------------------------------------------+
| TW DayTrade Monitor  12:11:22  #1                                         |
| TWII    46416 UP +0.8%  PrevC    46044  Age 12s   [IDX OK]                |
+---------------------------------------------------------------------------+
| Sym     Price   Chg%    VWAP Dir  dVWAP  Mom%  Vol Set R:R/Ri  Age Signal |
+---------------------------------------------------------------------------+
| 2303    178.0  +0.0%   178.6 DN   -0.3%  +0.0% 0.5L PUL 1.5R/0  13s [PASS] |
|  Reason: VWAP falling or flat                                              |
| 2327   1100.0  +4.8%  1082.9 UP   +1.6%  +0.0% 0.8  --     --  11s [WAIT] |
|  Reason: waiting for BRK or PUL trigger                                    |
| 2344    217.5  +6.1%   217.4 UP   +0.0%  +0.0% 0.7L PUL 1.5R/1  11s [CALL] |
|  Reason: PUL setup, market OK                                             |
+---------------------------------------------------------------------------+
| Action                                                                     |
|   >> 2344 PUL Buy~217 SL=215 TP=221 R:R=1.5 Risk=1.1%                     |
+---------------------------------------------------------------------------+
  2303=聯電  2327=國巨  2408=南亞科  2344=華邦電  TWII=加權指數
  信號: [CALL]=進場  [WAIT]=等買點  [BLK]=大盤擋  [PASS]=不作  [STALE]=舊資料  [CLOSE]=過13:15
  欄位: Set=BRK突破/PUL拉回/EXT過度延伸  R:R=風險報酬比  Age=資料年齡  Risk%=停損距離%

  更新 12:11:20 | 週期 30s | 來源 FinMind(real-time) | log: paper_trades.csv
```

### 區塊說明

| 區塊 | 位置 | 用途 |
|------|------|------|
| 大盤行 | 第 2 行 | 加權指數即時狀態，決定今天要不要做 |
| 個股表 | 中間 | 每檔股票的數據與信號 |
| Action | 下半部 | 有信號時的進場建議 |
| 對照表 | 框外 | 股票代碼與中文名對應 |
| 圖例 | 框外 | 信號與過濾的說明 |
| 狀態列 | 最底 | 更新時間、資料來源、錯誤訊息 |

---

## 欄位定義

### 個股行

| 欄位 | 意義 | 判讀方式 |
|------|------|----------|
| Sym | 股票代碼 | 對照框外對照表看中文名 |
| Price | 最新成交價 | 綠=強勢（價>VWAP且向上），紅=弱勢 |
| Chg% | 今日漲跌幅 | 綠=漲，紅=跌 |
| VWAP | 今日成交量加權均價 | 多空分界線 |
| Dir | VWAP 方向 | UP=向上，DN=向下，--=持平 |
| dVWAP | 現價距 VWAP 的百分比 | 正=在 VWAP 上方，負=下方。>2% = 過度延伸 |
| Mom% | 動能（與上次更新比） | 正=上漲中，負=下跌中 |
| Vol | 量比（今日 vs 昨日） | H=爆量(>1.5x)，L=量縮(<0.7x)，無標記=正常 |
| Set | 進場模式 | BRK=突破，PUL=拉回，EXT=過度延伸不追，--=無 setup |
| R:R/Risk | 風險報酬比 / 停損距離% | 例：1.5R/1.1% = 報酬比1.5倍、停損距進場1.1%。紅=停損>5% |
| Age | 資料年齡 | 幾秒/幾分鐘前。紅=>60s(stale)，黃=>30s |
| Signal | 最終信號 | 見下方信號說明 |

### Reason 行

每檔股票下方顯示「為什麼是這個信號」：

| Reason | 意思 |
|--------|------|
| BRK setup, market OK | 突破條件達標，大盤健康 |
| PUL setup, market OK | 拉回條件達標，大盤健康 |
| waiting for BRK or PUL trigger | 方向對但買點未到 |
| blocked by index | 個股OK但大盤弱 |
| too extended, don't chase | dVWAP > 2%，不追 |
| VWAP falling or flat | VWAP 方向不符合 |
| below VWAP | 價在 VWAP 下方 |
| data stale | API 失敗，資料過時 |
| after 13:15 no new entry | 過收盤保護時間 |

---

## 信號說明

| 信號 | 顏色 | 意義 | 動作 |
|------|------|------|------|
| `[CALL]` | 綠 | Dir + (Brk 或 Pul) + Idx 全過，且非 EXT | 進場 |
| `[WAIT]` | 黃 | Dir + Idx 過，但買點未到 | 等待 |
| `[BLK]` | 紫 | Dir 過但 Idx 不過 | 等大盤轉強 |
| `[PASS]` | 灰 | Dir 不過 或 過度延伸 | 不做 |
| `[STALE]` | 紅 | API 失敗，顯示舊資料 | 不信任此信號 |
| `[CLOSE]` | 黃 | 13:15 後，禁止新進場 | 平倉，不做新單 |

### 信號流動圖

```
13:15 後?  → [CLOSE] 不做新單
大盤 NODATA? → 不放行 (idx required)
大盤 NG?    → 個股最多 [BLK]
大盤 OK?
  └─ 個股 Dir 過?
      ├─ 否 → [PASS]
      └─ 是
          └─ dVWAP > 2% (EXT)?
          │   └─ 是 → [PASS] too extended
          └─ Brk 或 Pul 過?
              ├─ 否 → [WAIT]
              └─ 是 → [CALL] 進場
```

---

## 操作流程

### 第一步：看大盤

看面板最上面的大盤行：

- `[IDX OK]` 綠 → 大盤健康，可以做事
- `[IDX NG]` 紅 → 大盤弱，全部不開倉

**規則：大盤 NG → 今天不出手。沒有例外。**

### 第二步：找信號

掃描個股最右邊的 Signal 欄：

1. 找 `[CALL]` 綠色 → 直接看 Action 區進場
2. 沒有 CALL → 找 `[BLK]` 紫色 → 等大盤轉強
3. 都沒有 → 今天觀望

### 第三步：進場

看 Action 區的建議，有兩種模式：

#### 模式 A：突破追價 (BRK)

```
>> 2303 BRK Buy>=186 SL=173 TP=206 R:R=1.5 Risk=7.0%
```

| 項目 | 說明 |
|------|------|
| Buy>=186 | 現價突破開盤區間上緣 186 時買進 |
| SL=173 | 停損設在開盤區間低點 173 |
| TP=206 | 第一目標 = or_h + (or_h - or_l) × 1.5 |
| R:R=1.5 | 報酬風險比 1.5 倍 |
| Risk=7.0% | 停損距進場 7%。紅色 = >5% 要謹慎 |

進場條件：
- 現價突破開盤區間上緣
- 量比 > 1.5（有 H 標記）
- dVWAP < 2%（非過度延伸）
- 大盤同時是 OK

#### 模式 B：拉回低接 (PUL)

```
>> 2344 PUL Buy~217 SL=215 TP=221 R:R=1.5 Risk=1.1%
```

| 項目 | 說明 |
|------|------|
| Buy~217 | 價格回到 VWAP 附近量縮止穩時買 |
| SL=215 | 停損設在 VWAP 下方 1% |
| TP=221 | 第一目標 |
| R:R=1.5 | 報酬風險比 1.5 倍 |
| Risk=1.1% | 停損距進場 1.1% |

進場條件：
- 價格在 VWAP 上下 0.5% 內
- 量比 < 0.7（有 L 標記，量縮）
- VWAP 方向向上
- 大盤同時是 OK

### 第四步：進場後管理（完全人工）

面板**只負責進場訊號**，不追蹤持倉、不發出場提醒、不記錄部位。進場後的出場與停損完全靠人工執行，13:15 後面板會停止產生新 `[CALL]`（改顯示 `[CLOSE]`）。以下為人工操作規則：

| 事件 | 你要做什麼 |
|------|-----------|
| 到達 TP（Action 給的目標） | 賣出 50%，剩餘自行設移動停利 |
| 從最高點回檔 1% | 賣出剩餘部位 |
| 跌破 SL | 不猶豫，全部出場 |
| 信號從 [CALL] 變 [PASS] | 自行決定是否出場 |
| 13:15 面板顯示 [CLOSE] | 自行平倉，不留倉 |

---

## 完整操作範例

```
11:00 開面板
  大盤 [IDX NG] → 不出手，繼續盯

11:30 大盤翻 [IDX OK]
  看 BLK 的股票
  2303 本來 [BLK]，現在四項全綠 → [CALL]
  Action: >> 2303 Breakout | Buy>=186 SL=173 TP=206
  2303 現價 186.5 → 買進
  掛停損 173

12:00
  2303 漲到 206 → 到達 TP
  賣掉 50%，剩 50% 設移動停利（之後從高點回檔 1% 出）

12:30
  2303 從 210 回到 208 → 移動停利觸發 → 全出
  收工
```

---

## config.json 設定

```json
{
  "refresh_seconds": 30,
  "stocks": [
    {"symbol": "2303", "name": "聯電"},
    {"symbol": "2327", "name": "國巨"},
    {"symbol": "2408", "name": "南亞科"},
    {"symbol": "2344", "name": "華邦電"}
  ],
  "index": {
    "symbol": "001",
    "name": "加權指數"
  },
  "strategy": {
    "breakout_volume_ratio": 1.5,
    "pullback_vwap_range_pct": 0.5,
    "pullback_volume_ratio": 0.7,
    "index_vwap_required": true,
    "brk_confirm_bars": 2
  }
}
```

### 可調整項目

| 欄位 | 說明 | 預設 |
|------|------|------|
| refresh_seconds | 更新頻率（秒） | 30 |
| stocks | 監控股票清單 | 4 檔 |
| index.symbol | 大盤代碼（FinMind 用 001） | 001 |
| strategy.breakout_volume_ratio | 突破所需量比 | 1.5 |
| strategy.pullback_vwap_range_pct | 拉回距 VWAP 容許範圍 (%) | 0.5 |
| strategy.pullback_volume_ratio | 拉回所需量縮比 | 0.7 |
| strategy.index_vwap_required | 是否啟用大盤保護 | true |
| strategy.brk_confirm_bars | 突破需連續確認次數 | 2 |

### 新增股票

在 `stocks` 陣列加一行：

```json
{"symbol": "2330", "name": "台積電"}
```

### 關閉大盤過濾

在 config.json 中，`strategy` 底下加 `index_vwap_required: false`。注意必須保留其他 strategy 欄位：

```json
"strategy": {
  "breakout_volume_ratio": 1.5,
  "pullback_vwap_range_pct": 0.5,
  "pullback_volume_ratio": 0.7,
  "index_vwap_required": false
}
```

---

## 速查卡

```
大盤 NODATA? → 不放行
大盤 NG?     → 關面板
大盤 OK?     → 看個股

個股 [CALL]  → 看 Action 進場，注意 Risk%
個股 [WAIT]  → 等，不追
個股 [BLK]   → 等大盤轉強
個股 [PASS]  → 看 Reason 決定
個股 [STALE] → 資料過時，不信任
個股 [CLOSE] → 13:15 過了，平倉

Set = BRK 突破 / PUL 拉回 / EXT 過度延伸不追
R:R = 報酬風險比，Risk% = 停損距離 (>5% 紅色)
Age = 資料年齡 (>60s 紅色 = stale)

進場後：
  到 TP  → 賣一半
  回 1% → 全出
  破 SL → 全出
  13:15 → 全出
```

---

## 資料來源

| 資料 | 來源 | 延遲 |
|------|------|------|
| 即時報價（現價/VWAP/量比） | FinMind `taiwan_stock_tick_snapshot` | ~10 秒 |
| 開盤區間 | 盤中累積 snapshot 高低點（09:00-09:30） | 即時 |
| 大盤保護 | 指數 `close > 昨收` + 動能向上（vs 前次 snapshot） | ~10 秒 |
| 模擬交易記錄 | 每次 `[CALL]` 自動寫入 `paper_trades.csv` | 即時 |

注意：FinMind 指數 snapshot 不提供 `average_price`（VWAP），因此大盤保護用「指數 > 昨收 且 動能向上」代替。比真正的指數 VWAP 稍弱（單一基準點），但比「close > open」更不受跳空誤導。

FinMind Sponsor 到期後即時資料會失效，狀態列會顯示錯誤訊息（HTTP 401/402 等）。

### Paper Trade Log

每股每天首次出現 `[CALL]` 時，自動記錄一行到 `paper_trades.csv`（同一天同一檔不重複記錄）：

```
time, symbol, setup, entry, sl, tp, rr, risk_pct, price, vwap, or_h, or_l, vol_ratio, idx_price, idx_prevC, chg_pct
```

可用於事後回測、驗證策略是否有 edge。

**注意：** paper P&L 假設「以信號價成交」——BRK entry 用開盤高、PUL entry 用 VWAP。實單會有滑價，尤其 BRK 在連續確認 2 次（~60 秒）後才觸發，實際成交價已高於記錄的 entry，BRK 績效會被高估。回測時需將滑價納入考量。

---

## 風險聲明

本工具僅提供技術面輔助判斷，不構成投資建議。當沖交易具高風險，請在自身風險承受範圍內操作。
