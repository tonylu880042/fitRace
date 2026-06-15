# FitRaceStudio 專案產出計畫與路線圖 (ROADMAP.md)

本文件依據 `README.md` 的規劃，並遵循 `AGENT.md` 中規定的 **Clean Architecture (乾淨架構)** 與 **TDD (測試驅動開發)** 流程，詳細規劃本專案各階段的產出物（Deliverables）、模組設計、測試策略與驗證標準。

---

## 專案開發階段概覽

```text
+-----------------------+      +-----------------------+      +-----------------------+      +-----------------------+
|  Phase 1: 本地模擬    | ---> |  Phase 2: 場館原型    | ---> |  Phase 3: 現場試點    | ---> |  Phase 4: 產品化部署  |
|  - Mock Telemetry     |      |  - Real BLE/FTMS      |      |  - 積分排行榜邏輯     |      |  - systemd 系統服務   |
|  - MQTT Telemetry     |      |  - Central Soft AP    |      |  - SQLite 數據儲存    |      |  - 安全性硬化 (Auth)  |
|  - WebSockets Stream  |      |  - Setup App 原型     |      |  - 競賽結果導出       |      |  - OTA 自動更新機制   |
+-----------------------+      +-----------------------+      +-----------------------+      +-----------------------+
```

---

## Codebase Review Improvement Track

以下改善項目來自目前程式碼審查結果，應優先於新增大型功能處理。目標是先穩定 API 邊界、測試執行方式與核心狀態管理，避免後續 Phase 2/3 功能堆疊在脆弱基礎上。

### P0: 安全與正式環境防護
- **移除或限制測試遙測端點**：
  - 目前 `/api/test/telemetry` 在正式 app 中無條件開放，會讓任何可連到 Hub 的用戶注入遙測資料、註冊節點並影響比賽狀態。
  - 改善方向：僅在 `TESTING=1` 或明確的 development mode 中註冊此端點；正式環境改由 MQTT ingestion 路徑接收資料。
  - 驗證標準：正式設定下呼叫 `/api/test/telemetry` 回傳 `404` 或不可用；測試環境仍可使用該端點完成整合測試。
- **強化 Avatar 上傳驗證**：
  - 目前報名 API 接收任意 base64 bytes 並直接寫成 `.webp` 檔案，缺少大小限制與內容驗證。
  - 改善方向：限制 payload 大小、使用嚴格 base64 decode、驗證 MIME/header 與實際圖片格式，並拒絕空檔或超大檔。
  - 驗證標準：新增 API tests 覆蓋非法 base64、錯誤 MIME、超大圖片與合法 WebP 圖片。

### P1: RaceManager 狀態邊界整理
- **停止從 API/MQTT adapter 直接修改 private fields**：
  - 目前 `app.py` 與 `mqtt_subscriber.py` 直接操作 `RaceManager._registered_nodes`、`_progress`、`_stations`、`_active_nodes`。
  - 改善方向：在 `RaceManager` 新增 public methods，例如 `record_active_node()`、`ensure_node_registered()`、`ingest_telemetry()`，讓所有狀態轉換集中在 usecase 層。
  - 驗證標準：API 與 MQTT adapter 不再直接存取 `_` 開頭欄位；既有 race state、station、avatar、websocket tests 全部通過。
- **補齊 RaceConfig 驗證**：
  - 目前 `race_type` 接受任意字串，未知賽制可能導致進度計算與自動停止邏輯不一致。
  - 改善方向：將 `race_type` 改為 enum 或 `Literal["distance", "time", "calories", "max_power", "watts"]`，並依賽制驗證 `target_value` 或 `duration_sec` 必填且大於 0。
  - 驗證標準：新增 invalid config tests；未知 race type 回傳 `400`，不會進入 `READY/RUNNING` 狀態。

### P1: 測試與專案工具鏈
- **新增 Python 專案設定檔**：
  - 目前 repo 缺少 `pyproject.toml` 或 requirements，且直接執行 `.venv/bin/pytest -q` 會因 import path 失敗；需使用 `PYTHONPATH=. .venv/bin/pytest -q` 才會通過。
  - 改善方向：建立 `pyproject.toml`，記錄 dependencies、dev dependencies、pytest `pythonpath = ["."]`、Black/Ruff 設定。
  - 驗證標準：全新環境可依文件安裝依賴；執行 `pytest -q` 即可通過所有測試。
- **整理版本控制忽略規則**：
  - 目前工作樹包含 `.venv/`、`__pycache__/`、`wifi_state.json` 等生成檔。
  - 改善方向：新增或更新 `.gitignore`，忽略虛擬環境、Python cache、測試輸出、現場 Wi-Fi 狀態檔與 runtime avatar 檔案。
  - 驗證標準：`git status --short` 不再顯示生成檔；只顯示有意義的原始碼、測試與文件變更。

### P2: 遙測解析與可靠性
- **FTMS parser 加入長度邊界檢查**：
  - 目前 parser 只檢查最小長度，當 flags 宣告更多欄位但封包截斷時，可能讀出錯誤值或丟出不一致的例外。
  - 改善方向：加入共用 read helper，在每個欄位讀取前確認剩餘 bytes 足夠，並回傳明確 `ValueError`。
  - 驗證標準：新增 truncated packet tests，確認錯誤訊息可診斷且不會產生部分錯誤資料。
- **WebSocket broadcast 連線清理更穩定**：
  - 目前 broadcast 在迭代 `active_connections` 時同步移除失敗連線。
  - 改善方向：改為對連線快照迭代，收集失敗連線後再移除，避免多連線時跳過某些 client。
  - 驗證標準：新增多 WebSocket client 測試，模擬其中一個斷線時其他 client 仍收到廣播。

### 建議執行順序
1. 先完成 P0 安全項目，避免測試端點與檔案上傳在現場網路中形成風險。
2. 接著整理 `RaceManager` public API，讓 Phase 2/3 的節點監控、報名與排行榜功能有穩定狀態邊界。
3. 補上 `pyproject.toml`、`.gitignore` 與 pytest 設定，讓任何人或 agent 都能重現測試環境。
4. 最後處理 parser 與 WebSocket reliability，提升真實設備與大螢幕連線的現場穩定度。

---

## Phase 1: 本地模擬階段 (Local Simulation)

### 1.1 階段目標
在無實體有氧設備的狀況下，完成整個系統的核心資料流串接與基礎競賽邏輯驗證。

### 1.2 主節點 (Central Hub) 產出細化
* **運動器材連線資料匯總**：
  - 設計 `hub_server/adapters/mqtt_subscriber.py` 訂閱並解析各運動器材連線節點發送的 MQTT 遙測訊息。
  - 將各通道數據於記憶體內進行實時資料匯總（Consolidation），產出整合後的實時遙測封包。
* **基礎比賽建立與啟動**：
  - 實作 `hub_server/usecases/race_manager.py` 提供基礎比賽控制邏輯（建立比賽、啟動比賽）。
  - 提供 `/api/race/configure` 與 `/api/race/start` 等 REST API 端點。
* **比賽進度與結果廣播**：
  - 透過 `hub_server/adapters/websocket_manager.py` 串接 `/ws/dashboard` 端點。
  - 於比賽進行中（RUNNING 狀態）實時廣播比賽進度封包，並在比賽停止（STOPPED）時廣播比賽最終結果。

### 1.3 運動器材連線節點 (Edge Node) 產出細化
* **連線取值 (Mock 模擬)**：
  - 實作 `edge_node/usecases/mock_generator.py` 產生運動器材（跑步機、風扇車等）的模擬速度、轉速與功率資料。
* **資料發布**：
  - 實作 `edge_node/adapters/mqtt_publisher.py` 將資料打包發送至 MQTT Broker。

### 1.4 TDD 測試與驗證物
- `tests/unit/hub/test_race_state.py`: 驗證比賽狀態從 `IDLE` -> `READY` -> `RUNNING` -> `STOPPED` 的狀態轉換機制。
- `tests/unit/edge/test_telemetry_normalization.py`: 驗證模擬器產出的數據欄位完全符合標準遙測協議規格。

---

## Phase 2: 場館原型階段 (Studio Prototype)

### 2.1 階段目標
連接真實的 BLE 有氧設備，並在 Central Hub 啟動本地設定熱點 (Soft AP)，允許技術人員透過設定 App 進行設備綁定與 Wi-Fi 連線設定。

### 2.2 主節點 (Central Hub) 產出細化
* **SoftAP 設定與配置**：
  - 實作 `hub_server/infrastructure/network/soft_ap.py` 在設定模式下開啟 `FitRaceStudio_Setup` 本地 Wi-Fi 熱點。
  - 提供 `http://192.168.50.1/setup` 作為配置管理中心，接收來自 App 的 Studio Wi-Fi 設定、綁定設定。
* **運動節點連線狀態回報與監控**：
  - 實作 `hub_server/usecases/node_monitor.py` 持續接收並監控運動節點的線上狀態（Online Heartbeat）。
  - 當節點無資料更新或失去 Heartbeat 超過設定時間（如 5 秒），自動將節點狀態標記為 `offline` 並推播至大螢幕。

### 2.3 運動器材連線節點 (Edge Node) 產出細化
* **連線狀態管理**：
  - 實作 `edge_node/infrastructure/mqtt/client.py` 維持 MQTT 連線狀態，並定時發送 Node 存活心跳（Heartbeat）。
  - 實作斷線自動重連機制。
* **連線運動器材設定**：
  - 支援讀取來自本地或 Central Hub 下發的設備連線設定檔（例如指定連接的 FTMS 藍牙 MAC 位址或設備名稱）。
* **連線取值 (真實 BLE / FTMS)**：
  - 實作 `edge_node/infrastructure/ble/bleak_client.py` 建立與有氧器材的 BLE 連接。
  - 訂閱並解析 FTMS 服務（如 Indoor Bike Data, Treadmill Data）特徵值，實時提取實際運動數據。

### 2.4 設定 App (Setup App) 產出細化
* **手機 App 藍牙配置 Edge Node**：
  - 現場部署人員利用手機 App，透過藍牙直接與未配置的 Edge Node（樹莓派）建立連線。
  - 樹莓派在偵測到本地無設定檔時，自動啟動為 BLE Peripheral 廣播（名稱如 `FitRaceEdge_Setup`）。
  - 技術人員在手機 App 上設定並下發配置資訊（包括 `node_id`、`equipment_id`、`equipment_type`、器材的 `ble_target` 以及場館 WiFi SSID 與密碼）。
  - Edge Node 接收後將設定寫入本地 `config.json`，隨即重啟套用並進入正常工作模式（連接器材並透過 MQTT 發送數據）。
* **取值測試與狀態監控**：
  - 提供即時訊號測試頁面，App 可實時「取值」並顯示當前綁定運動節點的藍牙訊號強度 (RSSI) 與實時遙測數值，以驗證裝機正確性。

### 2.5 TDD 測試與驗證物
- `tests/unit/edge/test_ble_ftms_parser.py`: 使用 Mock 藍牙數據包，驗證風扇車/跑步機的二進位特徵值轉譯器是否能正確解出實時運動數據。
- `tests/unit/hub/test_soft_ap_config.py`: 測試 Hub 接收 App 的 Wi-Fi 配置後，寫入配置檔案並自動套用的邏輯。

---

## Phase 3: 現場試點階段 (Field Pilot)

### 3.1 階段目標
導入完整的賽事流程與積分計算法規則，提供本地 SQLite 資料庫作為賽事保存，支援實際競賽活動的營運。

### 3.2 主節點 (Central Hub) 產出細化
* **比賽建立與報名**：
  - 擴充 `hub_server/usecases/race_manager.py` 的賽事控制能力。
  - 提供比賽建立（設定賽制：如時間挑戰賽、距離挑戰賽）、比賽報名（將運動節點與參賽運動員/車隊進行綁定映射）。
* **設備數字編號與站位綁定 API**：
  - 支援對系統中所有在線的有氧設備進行數字編號（例如 1, 2, 3）。
  - 提供 `POST /api/race/register` API，接收來自選手手機的自助報名請求，將選手姓名與指定的數字編號站位綁定，並動態更新 `RaceManager` 狀態。
* **比賽進度實時排序與積分計算**：
  - 實作 `hub_server/usecases/leaderboard_calculator.py`。
  - 依據不同賽制，每 500ms 即時計算每位選手的「比賽進度」（如已完成距離百分比、即時功率），並輸出滾動排行榜排名。
* **比賽結果存檔與導出**：
  - 實作 `hub_server/adapters/db_repository.py` 將比賽最終成績（如完賽時間、總消耗卡路里、平均功率等）寫入本地 SQLite 資料庫。
  - 提供匯出 CSV 或 JSON 比賽結果報表的功能。
* **多國語系 (i18n) 字典服務**：
  - 於 `hub_server/infrastructure/` 建立 `locales/` 目錄，存放中英文語言字典檔（如 `zh_tw.json` 與 `en.json`）。
  - 提供 `/api/locales/{lang}` API 端點，允許前端獲取對應的詞條對照表。

### 3.3 設定 App (Setup App / Race Control Desk) 產出細化
* **設備數字編號配置**：
  - 主控台支援對系統中所有在線的有氧設備進行簡單數字編號（例如 1, 2, 3 等），以便與現場物理站位對應。
* **方案 B 自助掃碼報名系統**：
  - 主控台能為每個已編號的設備站位生成專屬的報名 QR Code，其連結結構為 `http://<hub_ip>:8000/signup?station=<station_number>`。
  - 選手透過手機掃碼，進入自助報名頁面並輸入姓名，即可將自身身分與該數字編號站位綁定。
* **建立比賽與管理控制台**：
  - 提供現場大會裁判/教練專用的 Web 介面。
  - 支援現場「建立比賽」、進行「站位分配監控」、手動「啟動/暫停/終止比賽」。
  - 顯示所有節點狀態、站位編號與現場即時遙測彙總表。
* **大螢幕看板與中控台多國語系切換**：
  - 看板 (`index.html`) 與主控台 UI 支援多國語系選單。
  - 前端從 `/api/locales/{lang}` 動態拉取詞條進行 UI 元素替換，預設為繁體中文，支援動態切換至英文。

### 3.4 TDD 測試與驗證物
- `tests/unit/hub/test_leaderboard_logic.py`: 測試各種賽制的計分與排名邏輯（例如同分、同距離時的排名優先權）。
- `tests/integration/hub/test_sqlite_persistence.py`: 測試 SQLite 資料庫的 CRUD 動作，以及比賽完成後成績的自動存檔功能。
- `tests/unit/hub/test_i18n_locales.py`: 測試中英文語言包字典檔案，驗證所有翻譯鍵（Keys）的對等性，防止詞條遺漏。
- `tests/unit/hub/test_signup.py`: 測試選手自助掃碼報名與站位編號綁定邏輯，驗證當多個選手同時對同一個編號站位報名時的覆蓋或排他規則。

---

## Phase 4: 產品化部署階段 (Productization)

### 4.1 階段目標
將系統打包為可交付的硬體鏡像與軟體套件，建立高可用性監控與運維機制。

### 4.2 部署與安全產出細化
- **系統服務化 (Systemd Services)**：
  - 封裝主節點服務 (`fitracestudio-hub.service`) 與運動節點服務 (`fitracestudio-edge.service`) 為系統守護進程，確保 RPi 開機自動啟動並內建崩潰自動重啟機制。
- **安全性硬化**：
  - 實作 API 安全權限機制，避免未授權用戶發送比賽控制指令或覆蓋 SoftAP 配置。
- **OTA 自動更新**：
  - 實作 RPi 的靜默 OTA (Over-the-Air) 版本更新檢驗機制。

---

## 本地 Git Commit 檢驗清單 (Checkpoint)

在每個階段的開發中，請嚴格按照以下流程進行 Commit：

1. **紅燈撰寫** -> `git commit -m "test: add failing tests for [FeatureName]"`
2. **綠燈實作** -> `git commit -m "feat: implement [FeatureName] to pass tests"`
3. **優化重構** -> `git commit -m "refactor: clean up [FeatureName] implementation"`

> [!CAUTION]
> 任何時候，若 `pytest` 執行結果含有任何紅燈，嚴禁進行本地 Commit。保持主幹分支 (main/master) 測試覆蓋率與綠燈狀態是本專案的最高原則。
