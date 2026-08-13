# FitRaceStudio 產品展示影片劇本

Updated: 2026-08-07 · 版本對應 v0.2.0

完整走查版劇本，總長約 **4 分鐘**，順序為：**比賽設定 → 比賽執行 → 系統設置與管理**。
45–60 秒的行銷短片策略另見 [VIDEO_DEMO_PLAN.md](VIDEO_DEMO_PLAN.md)；本檔是可直接照著錄的分鏡稿。

---

## 0. 錄製前置

**環境**

| 項目 | 設定 |
|---|---|
| Hub | `http://<hub>:8000`（本機錄製用 `localhost:8000` 即可） |
| 語系 | 全站切到 **中文**（gameAdmin / systemAdmin 右上 `EN / 中文`；Dashboard 由「系統設置 → 系統語系」） |
| 解析度 | 錄製 1920×1080；手機段落用 390×844 直立畫面另錄後嵌入 |
| 資料來源 | 無硬體時用 `/static/simulator.html`「一鍵配置場館 ⚡」產生 6 台設備與遙測；有硬體時走真實 Edge Node |
| 賽事設定 | 距離挑戰賽 500 m、個人賽、Classic 排行榜、開啟 `3, 2, 1, Go` |
| 選手 | 6 位、Station 1–6、含隊伍名與頭像（至少 2 位上傳自訂頭像） |

**分頁配置**（錄影前先開好，避免鏡頭裡出現載入空白）

1. `/` Dashboard（投影／大螢幕視角）
2. `/gameAdmin`
3. `/systemAdmin`
4. `/static/signup.html?station=3`（手機模擬）
5. `/static/simulator.html`（遙測驅動，**不入鏡**）

**遙測腳本要求**（讓比賽好看）

- 進度條全程可見移動
- 名次中途至少變動一次
- 最後 5 秒有一位選手拉開差距
- 完賽後立刻出現頒獎與紀錄牆

**注意事項**

- 電源段落**只錄到確認對話框，不要真的執行**。錄製機若未設 `FITRACE_POWER_COMMANDS_ENABLED=1`，按下去會回 200 但不動作，畫面看起來一樣「成功」，容易誤導觀眾。
- 錄影前關掉其他舊分頁，避免大量 WebSocket 重連造成 link badge 閃爍。

---

## Act 1 — 比賽設定（00:00 – 01:35）

### S1 開場定位 · 00:00–00:12

- **畫面**：Dashboard 空賽事狀態（`stage.waiting_setup` 等待設定），品牌配色與紀錄牆待機畫面。慢速推近。
- **字幕**：`FitRaceStudio — 場館級即時競賽系統`
- **旁白**：「FitRaceStudio 把場館裡的每一台有氧設備，變成一場可以即時計分的比賽。」

### S2 設備上線 · 00:12–00:30

- **畫面**：`/systemAdmin` → **Edge Nodes** 分頁。顯示節點在線、`0 online → N online`、每個節點下方的 telemetry stream 清單。
- **操作**：捲動一次節點卡片，游標停在在線徽章。
- **字幕**：`設備自動被發現`
- **旁白**：「開場前，技術人員只要確認 Edge Node 在線，設備的遙測串流就會自動被 Hub 發現。」

### S3 站位指派 · 00:30–00:50

- **畫面**：`/systemAdmin` → **Stations** 分頁 `Station Assignment`。
- **操作**：
  1. 從 `Unassigned telemetry streams` 選一條串流 → `Assign` 到 Station 1
  2. 展示 `Assign all to next open stations` 一次完成 2–6 站
  3. 游標帶過 `Copy Signup Link`
- **字幕**：`一鍵把設備對應到站位`
- **旁白**：「把遙測串流對應到站位號碼，比賽的座位表就完成了。每個站位都有自己的報名連結。」

### S4 選手報名（手機直立畫面） · 00:50–01:12

- **畫面**：手機開啟 Dashboard 上的 `掃碼自助報名` QR → `/static/signup.html?station=3`。
- **操作**：站位已自動帶入 Station 3 → 輸入姓名 → 輸入隊伍 → 選擇／上傳頭像 → `Register`。
- **回饋**：Dashboard 同步跳出 `歡迎 {name} 報名成功！已指派至 Station 3`。
- **字幕**：`選手用手機自己報名`
- **旁白**：「選手掃描站位 QR Code，填名字、隊伍、選頭像，三十秒完成報名，不需要櫃檯代打。」
- **剪輯**：手機畫面與 Dashboard 名單增加，做一次分割畫面。

### S5 教練設定賽制 · 01:12–01:35

- **畫面**：`/gameAdmin` → `Race Rules` 區塊。
- **操作**（每步停 1.5 秒讓觀眾看清）：
  1. `Race Type` → **Distance Challenge**
  2. `Target Distance (m)` → **500**
  3. `Competition` → **Individual Race**
  4. `Leaderboard View` → **Classic**（順手帶過 Race Track / Team Battle / Sprint Board）
  5. `Start Sound` → **Play 3, 2, 1, Go**
  6. 按 `Save Race`，`Unsaved Changes` 標記消失
- **字幕**：`教練只管比賽，不碰技術設定`
- **旁白**：「教練這一頁只做一件事：決定怎麼比。賽制、目標、個人或團體、排行榜長什麼樣，還有起跑音效。」

> 團體賽補充鏡頭（可選，+10 秒）：切到 `Team Race`，展示 `Team Scoring`（Team Average / Team Total）與 `Completion Rule`（Team Target / Everyone Finishes），下方 `Team Rule` 說明卡同步更新。

---

## Act 2 — 比賽執行（01:35 – 02:40）

### S6 起跑 · 01:35–01:50

- **畫面**：左 `gameAdmin`、右 Dashboard 分割畫面。
- **操作**：確認 `Station Status` 顯示各站就緒 → 按 **`Start Race`**。
- **重點**：Start 按鈕鎖定並顯示倒數狀態；Dashboard 全螢幕倒數 `3, 2, 1, Go`（聲音由 Dashboard 端播放，收音要收到）。
- **字幕**：`按下開始，大螢幕倒數`
- **旁白**：「教練按下開始，倒數與音效由大螢幕統一播放——選手不需要盯著螢幕等指令，計時從 Go 才開始。」

### S7 賽事直播 · 01:50–02:20

- **畫面**：Dashboard 全螢幕（切掉所有管理介面）。
- **內容**：即時排行榜、進度條、速度／功率／距離／卡路里、賽事計時器、名次交換動畫。
- **剪輯**：加速到 1.5×，保留最後 5 秒原速衝刺。
- **字幕**：`即時排名 · 即時進度`
- **旁白**：「比賽開始，每一台設備的數據每秒回傳，名次即時更新。這是整場活動最有感染力的畫面。」

### S8 完賽與成績 · 02:20–02:40

- **畫面**：
  1. Dashboard 完賽狀態 → `完賽！`／頒獎（金銀銅）→ `最終戰績`
  2. 紀錄牆更新為最近賽事
  3. 切到 `/static/results.html` 成績頁與賽後 QR
- **字幕**：`成績即時鎖定`
- **旁白**：「衝線後成績立刻鎖定，大螢幕播頒獎，選手掃碼就能看到自己的成績。」

---

## Act 3 — 系統設置與管理（02:40 – 03:45）

### S9 管理主控台總覽 · 02:40–02:52

- **畫面**：`/systemAdmin` → `Admin Console` 左側導覽：`Overview / Edge Nodes / Stations / Network / Software / Power Controls / Documentation / Support`。
- **操作**：Overview 卡片帶過 `Edge Nodes`、`Wi-Fi`、`Updates` 三個狀態摘要。
- **字幕**：`技術維運集中在 System Admin`
- **旁白**：「所有技術性操作都集中在 System Admin，和教練用的控制頁完全分開，現場不會有人誤按。」

### S10 網路設定 · 02:52–03:05

- **畫面**：**Network** 分頁 `Wi-Fi Network / wlan0`，顯示 SSID 與 IP。
- **操作**：點 `Choose Wi-Fi network` 展開可用網路清單（**不要真的切換**，展開後關閉）。
- **字幕**：`到場即可換網路`
- **旁白**：「換場地時，直接在網頁上把 Hub 接到現場的無線網路，不需要接螢幕鍵盤。」

### S11 軟體更新 · 03:05–03:22

- **畫面**：**Software** 分頁。
- **操作**：`Check Now` → 顯示版本與可用更新 → 帶過 `Download`、`Install Hub`、`Apply Hub`（標註 `IDLE only`），游標停在 `Allowed only while race is IDLE.` 提示。
- **字幕**：`更新只在賽事閒置時允許`
- **旁白**：「軟體更新分成檢查、下載、安裝、套用四步，而且只有在賽事閒置時才開放——比賽進行中不可能被更新打斷。」

### S12 電源與存取保護 · 03:22–03:38

- **畫面**：**Power Controls** 分頁：`Restart Hub Service / Reboot Hub / Shutdown Hub / Shutdown System`，接著展示 `Maintenance Unlock` 的 `維護解鎖` 存取碼對話框。
- **操作**：帶過四個電源按鈕與 `僅限 IDLE` 標記 → 點 `維護解鎖` → 存取碼輸入框 → 取消關閉。
- **注意**：`Reboot Hub` 的確認框是原生 `window.confirm`（見 [systemAdmin.html:2310](hub_server/static/systemAdmin.html:2310)），屬瀏覽器層級，**Playwright 的分頁錄影拍不到**，自動化版本不放這一拍。真要拍到確認框，得另外用螢幕錄影補一段。
- **字幕**：`危險操作需要解鎖`
- **旁白**：「重啟與關機這類操作需要確認，也可以用存取碼保護，避免現場人員誤觸。」

### S13 現場支援 · 03:38–03:45

- **畫面**：**Support** 分頁 → `Copy System Report`，畫面顯示系統快照收集完成。
- **字幕**：`一鍵匯出系統報告`
- **旁白**：「真的出狀況時，一鍵複製系統報告，工程師遠端就能判讀。」

---

## 收尾（03:45 – 04:00）

- **畫面**：Dashboard 紀錄牆／頒獎畫面慢動作，疊上三行重點。
- **字幕**：
  - `選手手機報名`
  - `教練一鍵開賽`
  - `技術維運全在瀏覽器`
- **旁白**：「一套系統，涵蓋報名、比賽、成績到維運。FitRaceStudio，讓場館裡的每一次訓練都變成比賽。」
- **收尾卡**：品牌 Logo + 版本號。

---

## 自動化錄製分鏡表

供腳本化錄影使用（Playwright／瀏覽器自動化）。輸出建議放 `output/videos/`。

| # | 場景 | 頁面 URL | 主要操作 | 秒數 | 字幕 |
|---|------|----------|----------|------|------|
| S1 | 開場 | `/` | 靜態推近 | 12 | 場館級即時競賽系統 |
| S2 | 設備上線 | `/systemAdmin#edge` | 捲動節點卡片 | 18 | 設備自動被發現 |
| S3 | 站位指派 | `/systemAdmin#stations` | Assign ×1、Assign all | 20 | 一鍵把設備對應到站位 |
| S4 | 選手報名 | `/static/signup.html?station=3` | 填名／隊伍／頭像／Register | 22 | 選手用手機自己報名 |
| S5 | 賽制設定 | `/gameAdmin` | Race Type→Target→View→Sound→Save | 23 | 教練只管比賽 |
| S6 | 起跑 | `/gameAdmin` + `/` | Start Race → 倒數 | 15 | 按下開始，大螢幕倒數 |
| S7 | 直播 | `/` | 遙測驅動、名次交換 | 30 | 即時排名 · 即時進度 |
| S8 | 完賽 | `/` → `/static/results.html` | 頒獎、紀錄牆、成績頁 | 20 | 成績即時鎖定 |
| S9 | 管理總覽 | `/systemAdmin` | Overview 摘要 | 12 | 技術維運集中在 System Admin |
| S10 | 網路 | `/systemAdmin#network` | 展開 Wi-Fi 清單（不切換） | 13 | 到場即可換網路 |
| S11 | 更新 | `/systemAdmin#software` | Check Now → 帶過更新流程 | 17 | 更新只在賽事閒置時允許 |
| S12 | 電源 | `/systemAdmin#power` | Reboot → 確認框 → 取消 | 16 | 危險操作需要解鎖 |
| S13 | 支援 | `/systemAdmin#support` | Copy System Report | 7 | 一鍵匯出系統報告 |
| S14 | 收尾 | `/` | 疊字收尾卡 | 15 | 三行重點 |

---

## English cut (EN)

同一套分鏡、同樣的秒數，UI 語系切 `en-US`，字幕與旁白換成下列英文。輸出到 `output/videos/en/`，
檔名同樣是 `s01_intro.webm` … `s14_outro.webm` 加 `demo_full_4min.mp4`。

| # | On-screen overlay | Narration |
|---|-------------------|-----------|
| S1 | Live racing for studios and events | FitRaceStudio turns every cardio machine in your venue into a live, scored race. |
| S2 | Equipment discovered automatically | Before the event, staff just confirm the Edge Nodes are online — telemetry streams are discovered for you. |
| S3 | Map equipment to stations in one click | Assign each stream to a station number and your seating chart is done. Every station gets its own signup link. |
| S4 | Athletes register from their phones | Athletes scan the station QR code, enter a name, a team, pick an avatar. Thirty seconds, no front desk. |
| S5 | Coaches control the race, not the wiring | This page does one thing: decide how you compete. Format, target, individual or team, leaderboard style, start sound. |
| S6 | Press start, the big screen counts down | The coach presses start. Countdown and audio play from the venue screen, and the clock only starts on Go. |
| S7 | Real-time ranking and progress | Every machine reports each second, and the ranking updates live. This is the moment the room reacts to. |
| S8 | Results locked instantly | Results lock the moment they finish. The screen plays the podium, and athletes scan for their own result. |
| S9 | All maintenance lives in System Admin | Every technical control sits in System Admin, fully separated from the coach's race desk. |
| S10 | Switch networks on arrival | Moving to a new venue? Connect the Hub to the local Wi-Fi from the browser — no screen or keyboard needed. |
| S11 | Updates only while the race is idle | Updates run in four steps, and only while the race is idle. A live race can never be interrupted by one. |
| S12 | Critical actions require unlock | Reboot and shutdown sit behind an access code, so nobody on site triggers them by accident. |
| S13 | One-click system report | If something does go wrong, copy the system report and an engineer can read it remotely. |
| S14 | Athletes register by phone / Coaches start with one press / Maintenance runs in the browser | One system — signup, racing, results, and maintenance. FitRaceStudio: every session becomes a race. |

**衍生短片**（同素材重剪，不需重錄）

- `01_overview.mp4`：S1 + S5 + S6 + S7 + S8 濃縮至 60 秒
- `02_signup.mp4`：S4 完整版，25 秒
- `03_game_admin.mp4`：S5 + S6，30 秒
- `04_system_admin.mp4`：S2 + S3 + S9–S13，60 秒
- `05_live_race.mp4`：S7 原速，20 秒
