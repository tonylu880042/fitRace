# 雲端報名 + 跑步機限時挑戰賽 規格

分支：`feat/cloud-signup-challenge`　狀態：待實作　日期：2026-10-06

## 1. 目標

客戶活動：**一台無動力跑步機**，參加者用自己的手機（行動網路）掃大螢幕 QR，
在雲端網頁填名稱＋照片後，Hub 自動倒數、跑 **3 分鐘**，依**距離**排名，
大螢幕只顯示**前 10 名**。

原則：雲端只負責「收報名資料」。倒數、計時、排名、成績保存全部留在 Hub（區網）。
Hub 只做 outbound HTTPS 拉取，不開任何對外 port。

## 2. 範圍

| # | 項目 | 層 |
|---|---|---|
| A1 | Hub 端限時賽截止（以 Hub 時鐘為準） | usecases + infrastructure |
| A2 | 挑戰模式：報名到齊自動倒數、結束後自動重置並重新套用賽制 | usecases + infrastructure |
| A3 | 頭像改為每筆報名一個穩定 URL（修閃爍、可供歷史排行使用） | usecases + infrastructure |
| A4 | 總排名 API 支援 `limit` 並帶 `avatar_url` | usecases + infrastructure |
| B1 | Vercel 報名頁 + API（`cloud_signup/`） | 新目錄 |
| B2 | Hub 拉取器 + 簽章 token + 報名佇列 + `signup_url` | usecases + adapters + infrastructure |
| C1 | 大螢幕：單人完賽畫面改為「本場距離＋總排名名次」 | static |
| C2 | 大螢幕：總排名只顯示前 10 名＋照片 | static |
| C3 | 大螢幕：QR 改用 state 的 `signup_url` | static |
| C4 | i18n：`stage.running_main` 的 `{raceType}` 顯示成 `TIME`（未翻譯） | static / locales |
| C5 | Game Admin：挑戰模式開關、秒數、重置延遲 | static |

不做：Edge 節點面板隱藏開關、多台器材各自獨立開跑、雲端端的速率限制。

## 3. 現況與已知缺陷（實作前必讀）

- **限時賽沒有 Hub 截止**：`RaceManager` 在所有參賽者的 `elapsed_time_ms >= duration_sec*1000`
  時才轉 STOPPED（`race_manager.py` ~1440），而 elapsed 優先取器材回報值
  （`_elapsed_time_ms`）。器材斷線或錶頭計時不同步時比賽永遠不會結束。
- **頭像閃爍**：`update_telemetry` 每次產生
  `/static/avatars/station_{n}.webp?t={int(time.time())}`，URL 每秒變，瀏覽器每秒重抓。
  且檔案以站位命名，下一位報名就覆蓋，歷史排行無法顯示照片；檔案寫進
  `hub_server/static/avatars/`（程式目錄、未 gitignore）。
- **自動重置在大螢幕前端**：`index.html` 的 `startAutoResetCountdown()` 由頁面 JS 觸發
  （違反「大螢幕只投影、行為由後端驅動」）。挑戰模式的重置必須在 Hub 後端。
  另外 `/api/race/reset` 會清掉 config，重置後要重新套用挑戰賽制。
- **倒數端點會阻塞**：`/api/race/countdown-start` 在 handler 內 `await asyncio.sleep(倒數)` 後才
  `start_race()`。自動開跑要把這段抽成可共用的 coroutine，HTTP 端點與自動流程共用。
- **單人頒獎台**：`showPodiumOverlay()` 單人時顯示「金牌」，與總排名不符。
- **測試遙測不存成績**：`/api/test/telemetry` 轉 STOPPED 時沒呼叫 `save_finished_snapshot`
  （MQTT 路徑有）。A1 的截止 tick 會統一處理儲存，順便讓這條路徑也正確。

## 4. 詳細需求

### A1 Hub 端截止
- 新增 `RaceManager.enforce_time_deadline(now_epoch_ms) -> bool`：RUNNING、非 mixed、
  `race_type in ("time","max_power","watts")`、`now >= start + duration*1000` 時：
  每位參賽者 `elapsed_time_ms` 封頂為 `duration*1000`、`progress_percent` 封頂 100、
  距離維持截止前最後值；`end_time_epoch_ms = start + duration*1000`；轉 STOPPED；回傳 True。
- 截止後到達的遙測不得改變任何成績（現有 STOPPED 早退保留）。
- 截止前到達、器材 `elapsed_time_ms` 已超過 duration 的封包，進度百分比也不得超過 100
  （目前畫面會出現 100.6%）。
- infrastructure：lifespan 背景任務每 250ms 呼叫一次；回傳 True 時走
  `broadcast_race_state()`（會存成績）。時間來源可注入，測試不得 sleep。

### A2 挑戰模式
- race settings 新增（持久化於 race_settings.json，比照現有設定）：
  `challenge_mode_enabled: bool = False`、`challenge_duration_sec: int = 180`、
  `challenge_reset_delay_sec: int = 15`。
- 啟用時：
  1. 任何報名（LAN 報名頁、雲端拉取）完成後，若 state 為 READY、所有已指派站位都已報名、
     且沒有倒數進行中 → 自動執行倒數開跑（與 `/api/race/countdown-start` 同一個 coroutine，
     含 readiness 檢查）。
  2. STOPPED 後 `challenge_reset_delay_sec` 秒 → 後端 reset，並以
     `race_type="time", duration_sec=challenge_duration_sec` 重新 configure → READY。
  3. 啟用挑戰模式本身也要立即套用賽制（IDLE → READY）。
  4. 未啟用時行為與現在完全相同（前端自動重置維持原樣）。
- 決策邏輯（何時該開跑、何時該重置）放 usecases，可用假時鐘單元測試；
  infrastructure 只負責排程與呼叫。
- API：沿用現有 settings 端點風格，`require_admin`；挑戰模式切換在 RUNNING 中禁止。

### A3 頭像儲存
- 報名時把頭像寫到資料目錄（與 race results 同層，例如
  `<results 目錄>/avatars/<uuid>.webp`），registration 記住該 id。
- 新端點 `GET /api/avatars/{id}.webp`（id 嚴格驗證 `[0-9a-f]{32}`，防路徑穿越）。
- progress / station 狀態中的 `avatar_url` 改為 `/api/avatars/<id>.webp`，**不帶時間戳**，
  同一筆報名整場不變。
- 成績 snapshot 會帶到 `avatar_url`，歷史排行可直接使用。
- 舊的 `static/avatars/station_N.webp` 路徑移除；`.gitignore` 不需新增（已不寫進程式目錄）。
- `# ponytail:` 不做清理，每人約 30KB；需要時再加保留天數。

### A4 總排名
- `GET /api/results/standings?limit=10`：每個 section 只回前 N 列（不給 limit = 現行為不變）。
- 每列加 `avatar_url`（無則 null）。

### B1 Vercel 報名頁（`cloud_signup/`）
- 結構：`cloud_signup/public/index.html`、`cloud_signup/api/claim.js`、
  `cloud_signup/lib/validate.js`（純函式，供 `node --test`）、`cloud_signup/README.md`（部署步驟）。
  零 npm 相依；`api/claim.js` 以 `fetch` 呼叫 Upstash Redis REST。
- 環境變數：`UPSTASH_REDIS_REST_URL`、`UPSTASH_REDIS_REST_TOKEN`。
- 網址參數：`?v=<venue>&s=<station>&t=<token>`。頁面欄位只有：名稱（必填，1–20 字）、
  照片（選填，拍照或相簿）、確認按鈕。送出後顯示「已報名，請看大螢幕」。
- 照片處理沿用 `hub_server/static/signup.html` 的壓縮流程（同尺寸、同格式），
  確保 Hub 現有 `decode_avatar_webp` 能吃。
- `POST /api/claim` 驗證：venue/station/token 格式、名稱長度、照片 data URL ≤ 200KB；
  通過後 `RPUSH fitrace:claims:<venue>` JSON
  `{id, venue, station, token, name, avatar_base64, received_at}`，
  並 `EXPIRE` 該 key 900 秒、`LTRIM` 保留最後 50 筆。**不驗 token 簽章**（雲端沒有密鑰）。
- i18n：雲端頁無法向 Hub 取翻譯，例外地在 `cloud_signup/public/locales/{zh-TW,en}.json`
  放自己的字串（只含此頁所需 key），頁面依 `navigator.language` 選擇，預設 zh-TW。
  頁面 HTML/JS 不得硬寫中英文字串。

### B2 Hub 拉取器
- 環境變數（全部存在才啟用，否則整個功能關閉、行為與現在相同）：
  `FITRACE_CLOUD_SIGNUP_URL`、`FITRACE_CLOUD_SIGNUP_SECRET`、`FITRACE_VENUE_ID`、
  `UPSTASH_REDIS_REST_URL`、`UPSTASH_REDIS_REST_TOKEN`。
- Token（usecases，純函式，stdlib `hmac`/`hashlib`）：
  `make_signup_token(secret, venue, station, exp_epoch_s)` → `"<exp>.<hex hmac 前 16 碼>"`；
  `verify_signup_token(..., now)` 檢查簽章（`hmac.compare_digest`）與未過期。
  有效期 600 秒，大螢幕每 60 秒換新。**token 不是一次性**（同一分鐘多人掃同一個 QR 必須都能報名）。
- `CloudSignupProcessor`（usecases）：注入 `fetch_claims()`、`register(station, name, avatar_b64)`、
  race 狀態查詢、時鐘。`tick()`：
  1. 拉取新 claim → 驗 venue、station 是否已指派、token → 不合格者丟棄並記 log
     （log 不得含照片內容）。
  2. 合格者進 FIFO 佇列（以 claim `id` 去重）。
  3. 若對應站位目前可報名（READY/IDLE 且未報名）→ 取佇列頭報名。比賽進行中的人留在佇列，
     等重置後自動輪到。
- Adapter `UpstashClaimSource`：一次 `LPOP key 10`（Upstash REST JSON body
  `["LPOP", key, "10"]`），stdlib `urllib`，在 `asyncio.to_thread` 中執行，timeout 5 秒；
  網路錯誤回傳空清單並記錄最後失敗時間。
- 背景任務每 1.5 秒 tick。
- race state 新增：`signup_url`：雲端啟用且最近 30 秒內拉取成功 →
  `<CLOUD_URL>?v=<venue>&s=<station>&t=<token>`；否則 → 現有區網報名網址（斷網 fallback）。
  另加 `cloud_signup_online: bool`、`cloud_signup_queue_length: int`。

### C 前端
- C1：比賽結束時若本場只有 1 位參賽者 → 完賽畫面顯示「照片＋名稱＋本場距離＋總排名第 N 名」
  （N 取自 `/api/results/standings`，比對 `race_start_epoch_ms` + `station_number`）；
  進前 10 名多一行「進入前 10 名！」。多人時維持現行頒獎台。
- C2：大螢幕總排名呼叫 `?limit=10`，每列顯示照片（無照片用現有預設樣式）。
- C3：大螢幕報名 QR 一律用 state 的 `signup_url`（現有 `/api/qr.svg` 產生）。
- C4：`{raceType}` 傳入翻譯後的 `race_type.*` 字串。
- C5：Game Admin 加挑戰模式區塊（開關、秒數、重置延遲），字串全部經 locales（zh-TW、en）。
  System Admin / Dashboard 不得出現這些控制。

## 5. 驗收

1. `pytest` 全綠；所有編輯過的 page JS 抽出後 `node --check` 通過；
   `node --test cloud_signup/` 通過。
2. 單元測試涵蓋：截止封頂與截止後遙測不影響成績；挑戰模式開跑／重置決策；
   token 簽章、過期、竄改；處理器的去重、FIFO、忙碌時保留佇列；standings limit；
   avatar id 驗證（`../`、非 hex 被拒）；Vercel 驗證函式。
3. 本機 demo：以 `scripts/demo_hub.py` 跑一台跑步機 3 分鐘挑戰，器材中途停送資料，
   比賽仍在 3:00 結束並存檔；之後自動回到 READY。

## 6. 部署備註
- Vercel 專案 root 設為 `cloud_signup/`，在 Vercel Marketplace 加 Upstash Redis。
- Hub 的 5 個環境變數加在 systemd unit；未設定時一切照舊。
- 部署前依慣例確認 hub venv 相依（本功能只用 stdlib，無新套件）。
