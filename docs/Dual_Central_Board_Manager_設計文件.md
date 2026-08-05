# Dual Central Board Manager — Pi 雙 UART 架構設計

Pi 同時管理兩個 BLE Central Board，各接一個 UART port。新增 GatewayManager 協調掃描、設備指派、統一資料流，並處理 8 類潛在問題。新增 UART FTMS JSON 格式、非 FTMS 回傳格式逐欄 Schema 兩個章節，方便 Pi 端 Python 開發同仁快速整合。

*Vmax GW BLE 2026 · 架構更新*

- **UART**：2 port
- **Central Board**：× 2 nRF52832
- **最大設備**：6 台（每 GW 最多 3）
- **對應韌體版本**：1.3.2
- **更新**：2026-07-29

## 目錄

- [應用場景重述](#應用場景重述)
- [系統架構](#系統架構)
- [程式邏輯流程](#程式邏輯流程)
- [UART FTMS JSON 格式](#uart-ftms-json-格式)
- [非 FTMS 回傳格式逐欄 Schema](#非-ftms-回傳格式逐欄-schema)
- [config.py 完整設計](#configpy-完整設計)
- [logging_setup.py 共用日誌模組](#loggingsetuppy-共用日誌模組)
- [設備分配策略](#設備分配策略)
- [GatewayManager](#gatewaymanager)
- [AlertBus — 警告系統](#alertbus--警告系統)
- [問題處理策略](#問題處理策略)
- [目錄結構（更新後）](#目錄結構更新後)
- [Pi 適配要點](#pi-適配要點)

---

## 應用場景重述

Pi 主機有兩個實體 UART port，分別連接兩個功能完全相同的 BLE Central Board（nRF52832）。每個 Central Board 各自掃描、連線、回傳 FTMS 資料。Pi 必須：

- 同時管理兩條 UART 的 BOOT / 掃描 / 連線 / 資料接收生命週期
- 在掃描結果中決定「哪台設備由 GW1 連線、哪台由 GW2 連線」
- 匯整兩個 Gateway 的 FTMS 資料流到統一的上層介面
- 偵測並通知各類異常（GW 離線、設備找不到、資料逾時、未預期重啟等）

### 物理連接示意

```text
左欄（GW1 所連設備）              中欄（Pi 主機）                     右欄（GW2 所連設備）
┌─────────────────────┐                                      ┌─────────────────────┐
│ TMILL #1             │                                      │ ROWER #1             │
│ AA:BB:CC:DD:EE:01     │                                      │ AA:BB:CC:DD:EE:03     │
├─────────────────────┤        ◁ BLE ▷                        ├─────────────────────┤
│ BIKE #1              │                                      │ ELLIP #1              │
│ AA:BB:CC:DD:EE:02     │                                      │ AA:BB:CC:DD:EE:04     │
└─────────────────────┘                                      └─────────────────────┘
          ▲                                                              ▲
          │ BLE                                                          │ BLE
          ▼                                                              ▼
                 ┌───────────────────────────────┐
                 │  Central Board 1 (GW1)          │
                 │  /dev/ttyAMA0 · nRF52832         │
                 └───────────────────────────────┘
                                 ↕ UART TTL 115200
                 ┌───────────────────────────────┐
                 │  Raspberry Pi                    │
                 │  GatewayManager                  │
                 └───────────────────────────────┘
                                 ↕ UART TTL 115200
                 ┌───────────────────────────────┐
                 │  Central Board 2 (GW2)          │
                 │  /dev/ttyAMA2 · nRF52832         │
                 └───────────────────────────────┘
```

---

## 系統架構

由上而下的分層架構（`↕` 表示雙向溝通）：

```text
┌──────────────────────────────────────────────────────────────┐
│ Application（demo_pi.py / Pi 系統程式）                          │
│ 呼叫 GatewayManager API，處理 FTMS 資料與警告                     │
└──────────────────────────────────────────────────────────────┘
                          ↕ GatewayManager API
┌──────────────────────────────────────────────────────────────┐
│ GatewayManager                                                  │
│ 協調兩個 GW · 掃描分配 · 統一資料流 · 警告分派 · 健康監控          │
└──────────────────────────────────────────────────────────────┘
                          ↕
┌────────────────────────────┐  ┌────────────────────────────┐
│ GatewayClient(GW1)           │  │ GatewayClient(GW2)           │
│ UART reader thread · callback │  │ UART reader thread · callback │
│ · blocking API                │  │ · blocking API                │
└────────────────────────────┘  └────────────────────────────┘
                          ↕
┌────────────────────────────┐  ┌────────────────────────────┐
│ /dev/ttyAMA0 → Central Board 1 (GW1) │ /dev/ttyAMA2 → Central Board 2 (GW2) │
└────────────────────────────┘  └────────────────────────────┘
```

> **注意：** GatewayClient 不修改。它只管一條 UART。GatewayManager 是新增的協調層，持有兩個 GatewayClient 實例，負責所有跨 GW 的邏輯。

---

## 程式邏輯流程

1. **雙 GW 平行初始化**
   兩個 GatewayClient 同時開 serial port，主動送出 `PING;`（每 2 秒重送）等待 Central Board 回應 BOOT 狀態。
   若任一 GW 在 boot_timeout 內無回應 → AlertBus 發出 `GW_INIT_FAILED`（CRITICAL）
   *v1.1.0：不再等待板子自發 BOOT，改由 Pi 主動 PING，解決 Pi 開機時序問題*

2. **判斷是否需要掃描**
   讀取兩個 BOOT 訊息：
   - GW1 `HAS_LIST` + GW2 `HAS_LIST` → 跳過掃描，等待 REPORTING
   - 任一 `NO_LIST` → 對該 GW 執行掃描流程
   - 強制重掃模式（config 設定）→ 先對各 GW 送 `DISCONNECT:ALL;` 清空 NVS MAC 清單，等待 OK 後才進行掃描

3. **平行掃描（需掃描的 GW）**
   兩個 GW 同時 `SCAN:START;`，各自收集 scan_duration 秒內的 DEVICE 訊息。
   掃描結束後比對是否有重複 MAC（→ `DUPLICATE_DEVICE` 警告），依策略解決。

4. **設備分配與連線**
   依 config 分配策略（STATIC / RSSI / HYBRID）決定每台設備連到哪個 GW。
   找不到目標 MAC → 重試掃描（最多 scan_max_retries 次）→ 仍找不到 → `DEVICE_NOT_FOUND`（WARNING）
   超過每 GW 3 台上限 → `MAX_CONN_EXCEEDED`（WARNING），截斷多餘 MAC

   > **注意（v1.3.1 起，連線前等待期）：** 板端掃描比對到目標 MAC 後，**不會立即發起 GATT 連線**，會先等滿 `UCM_BLE_CONNECT_GRACE_MS`（板端具名常數，目前固定 3000ms）才真正呼叫連線，期間持續掃描、`STATUS;` 仍停在 `SCAN`。原因是部分裝置打開廣播後尚未完全初始化完成，太早連線會讓裝置異常甚至當機。**這對 Pi 端是新增的固定延遲**：
   > - 從送出 `CONNECT:`/`CONNECT_ADD:` 到 `STATUS;` 真正變成 `REPORT`，至少會多 3 秒，`reporting_timeout`（等待 REPORTING 狀態的逾時，預設 30 秒）需預留這段時間，不要當成連線異常提前放棄；`cmd_timeout` 只管 `CONNECT:OK;` 這類指令 ACK 本身，不受影響。
   > - 每次設備意外斷線重連（見步驟 07 的 `BOOT_UNEXPECTED` 以外情境），板端也會重新歸零這個等待期，代表斷線後重連同樣要再等 3 秒，不是斷了就立刻重連上。

5. **等待 REPORTING + 設定回報週期**
   兩個 GW 均進入 REPORTING 後，設定各自的 report_interval_ms。

6. **長期運行：資料接收 + 健康監控**
   FTMS 資料從兩個 GW 流入，GatewayManager 加上 `gateway_id` tag 後統一回調。
   健康監控 thread 每 health_check_interval 秒對每個 GW 發 `STATUS;`，無回應 → `GW_OFFLINE`。
   資料計時器偵測每個已連線設備的最後資料時間，逾時 → `DATA_TIMEOUT`。

7. **異常處理與自動恢復**
   收到非預期 BOOT（GW 重啟）→ 對該 GW 重走步驟 02–05，另一 GW 不中斷。
   UART serial 斷線 → 嘗試 reconnect_attempts 次重連，間隔 reconnect_delay 秒。
   全部失敗 → `RECONNECT_FAILED`（CRITICAL）。

---

## UART FTMS JSON 格式

這是 Pi 端 Python 整合時實際會解析的資料格式——Central Board 韌體（`UCM_REPORT.c` + `UCM_BLE.c`）每個回報週期，對每台已連線且有資料的設備送出一行文字，內含一段 JSON。**GatewayClient 的 UART reader thread 只需處理這一種資料行格式即可取得所有 FTMS 數值。**

> **注意：** 完整封包格式（含結尾分號與換行）：
> `FTMS:<MAC>,<TYPE>,{json};\r\n`

### TYPE 欄位對應（ucm_ftms_device_type_t）

| TYPE 字串 | 對應列舉 | 設備類型 | JSON 內含欄位 |
|---|---|---|---|
| TMILL | FTMS_TYPE_TREADMILL | 跑步機 | instantaneous_speed / total_distance / instantaneous_power / total_energy |
| BIKE | FTMS_TYPE_INDOOR_BIKE | 飛輪 / 室內腳踏車 | instantaneous_speed / total_distance / instantaneous_power / total_energy |
| ROWER | FTMS_TYPE_ROWER | 划船機 | stroke_rate / total_distance / instantaneous_pace / instantaneous_power / total_energy |
| ELLIP | FTMS_TYPE_CROSS_TRAINER | 橢圓機 | instantaneous_speed / total_distance / instantaneous_power / total_energy |
| UNKNOWN | FTMS_TYPE_UNKNOWN | 解析失敗 / 未知 | 僅 rssi |

### 各設備類型的 JSON schema + 實際範例行

**TMILL / BIKE / ELLIP（速度型設備，三種類型 JSON 結構相同）**

`instantaneous_speed` 韌體已換算為 km/h（原始值 ÷ 100，兩位小數）；其餘欄位為 BLE FTMS 原始值：`total_distance` 公尺、`instantaneous_power` 瓦特、`total_energy` 大卡。

```json
{
  "rssi": -55,
  "instantaneous_speed": 8.32,
  "total_distance": 1204,
  "instantaneous_power": 142,
  "total_energy": 37
}
```

實際 UART 一行（TMILL 範例）：
```text
FTMS:AA:BB:CC:DD:EE:01,TMILL,{"rssi":-55,"instantaneous_speed":8.32,"total_distance":1204,"instantaneous_power":142,"total_energy":37};
```

**ROWER（划船機，多一個 stroke_rate / pace，沒有 instantaneous_speed）**

`stroke_rate` 韌體已換算為 次/分（原始值 ÷ 2，0.5 解析度，只會是 .0 或 .5）；`instantaneous_pace` 為 BLE FTMS 原始值（秒 / 500 公尺）。

```json
{
  "rssi": -60,
  "stroke_rate": 24.5,
  "total_distance": 850,
  "instantaneous_pace": 125,
  "instantaneous_power": 98,
  "total_energy": 22
}
```

實際 UART 一行（ROWER 範例，真實 BLE FTMS 划船機）：
```text
FTMS:AA:BB:CC:DD:EE:03,ROWER,{"rssi":-60,"stroke_rate":24.5,"total_distance":850,"instantaneous_pace":125,"instantaneous_power":98,"total_energy":22};
```

> **注意（2026-07-07 起）：** 走 Delightech 協定的划船機（`UCM_DLTECH.c`，見「Delightech BT Protocol」章節）JSON 會**多一個 `stroke_count`** 欄位（累計划槳次數），因為該協定的 stroke_rate 與 stroke_count 剛好來自兩種輪流出現的封包，補齊後才一併上報：
> ```text
> FTMS:AA:BB:CC:DD:EE:04,ROWER,{"rssi":-60,"stroke_rate":24.5,"stroke_count":312,"total_distance":850,"instantaneous_pace":0,"instantaneous_power":98,"total_energy":22};
> ```
> 同樣掛 `TYPE=ROWER`，但欄位數依實體來源（真實 FTMS 裝置 vs. Delightech 裝置）不同，`instantaneous_pace` 在 Delightech 來源固定為 `0`（協定沒有配速欄位）。Python 端本來就該用 `dict.get()` 存取，不要假設 ROWER 一定有固定欄位數。
>
> **已知限制（v1.3.1，2026-07-14 診斷中，尚未解開）：** 實機重新測試某款 Delightech 跑步機（`74:46:B3:DB:48:49`）時，handshake（cmd `0x40`）從未成功、機型判斷卡在 `UNKNOWN`，資料完全沒有送到 Pi；同時觀察到協定文件未記載的 `cmd=0x41` 封包會在 CCCD 訂閱完成後立刻主動推播，與協定「Device 不會主動推播」的既有假設矛盾。板端目前只加了診斷用的 hexdump log（`UCM_DLTECH.c`），尚未修復。**若 Pi 端對接時發現某台 Delightech 裝置連上後一直卡在 `UNKNOWN` 或沒有 FTMS 資料，先假設是這個已知問題，不用懷疑自己的 parser。** 詳見 `CHANGELOG.md` 1.3.1 條目與 `DESIGN.md` §7.5「已知限制」。

**UNKNOWN / 解析失敗 fallback**

FTMS 資料解析失敗，或設備類型不在支援清單內時的最小回退格式，只保留 `rssi`。

```text
FTMS:AA:BB:CC:DD:EE:99,UNKNOWN,{"rssi":-70};
```

> **警告：** 欄位是否存在會依設備類型、甚至同一 TYPE 底下的實體來源（真實 FTMS 裝置 / Delightech 裝置）不同——ROWER 沒有 `instantaneous_speed`，其餘三種類型沒有 `stroke_rate` / `instantaneous_pace`；ROWER 的 `stroke_count` 只有 Delightech 來源才有。Python 端解析時務必用 `dict.get()` 或先檢查 TYPE 欄位，不要假設所有欄位都存在。

### Python 端最小解析範例（gateway_client 內部應有的邏輯）

```python
# gateway_client/protocol.py — 解析 FTMS 資料行
import json
import re

_FTMS_RE = re.compile(r"^FTMS:([0-9A-Fa-f:]+),(\w+),(\{.*\});?$")

def parse_ftms_line(line: str) -> dict | None:
    """解析一行 UART 收到的 FTMS 資料。回傳 None 表示格式不符，直接丟棄該行。"""
    line = line.strip().rstrip(";")
    m = _FTMS_RE.match(line)
    if not m:
        return None
    mac, type_str, json_part = m.groups()
    try:
        data = json.loads(json_part)
    except json.JSONDecodeError:
        return None          # UART 傳輸中可能截斷，直接丟棄，等下一筆
    data["mac"]  = mac
    data["type"] = type_str   # TMILL / BIKE / ROWER / ELLIP / UNKNOWN
    return data

# 用法：
# >>> parse_ftms_line('FTMS:AA:BB:CC:DD:EE:01,TMILL,{"rssi":-55,"instantaneous_speed":8.32,...};')
# {'rssi': -55, 'instantaneous_speed': 8.32, ..., 'mac': 'AA:BB:CC:DD:EE:01', 'type': 'TMILL'}
```

---

## 非 FTMS 回傳格式逐欄 Schema

前一節的 FTMS JSON 是「資料流」格式；本節補齊 BOOT / DEVICE / STATUS / OK / ERROR 這些「控制流」回應的逐欄定義與實際 UART 範例行，供 Pi 端寫 parser 時逐一比對。

### BOOT — 開機狀態查詢回應

v1.1.0 起改為**被動回應**：板子開機後不再自動推送 BOOT，需由 Pi 主動送 `PING;`（建議每 2 秒重送直到收到回應）查詢。

| 情境 | 格式 | 範例 |
|---|---|---|
| 板端已存有目標 MAC 清單 | `BOOT:HAS_LIST,count=<N>;` | `BOOT:HAS_LIST,count=2;` |
| 板端無存清單 | `BOOT:NO_LIST;` | `BOOT:NO_LIST;` |

- `count`：已存的目標 MAC 數，範圍 1–3（單板最多同時連線 3 台），只在 `HAS_LIST` 時出現。

> **警告：** 這是對 `PING;` 的回應，不是硬體自發訊息。若 Pi 在**沒有送 PING** 的情況下、於運行中途收到這個格式，代表板子非預期重啟（對應問題處理策略表中的 `BOOT_UNEXPECTED`），GatewayManager 應觸發該 GW 的重新初始化流程。

### DEVICE — 掃描階段逐台設備回報

`SCAN:START;` 之後，板子每偵測到一台符合條件（FTMS 廣播 + 內部去重）的設備就送一行，掃描期間可能連續收到多行：

`DEVICE:<MAC>,<RSSI>,<NAME>,<TYPE>;`

| 欄位 | 型別 | 說明 |
|---|---|---|
| MAC | string | `AA:BB:CC:DD:EE:01` 格式，大寫冒號分隔 |
| RSSI | int（dBm，負值） | 掃描當下量測訊號強度 |
| NAME | string，可能為空 | BLE 廣播名稱；設備未廣播名稱時為空字串，**欄位仍存在**（兩個逗號中間無字元） |
| TYPE | enum string | `UNKNOWN` / `TMILL` / `BIKE` / `ROWER` / `ELLIP`，判斷出的機種類型 |

實際 UART 範例：
```text
DEVICE:AA:BB:CC:DD:EE:01,-55,Treadmill-01,TMILL;
DEVICE:AA:BB:CC:DD:EE:99,-70,,UNKNOWN;
```

> **警告：** 第二個範例 NAME 欄位是空字串，不是欄位被省略——用逗號分割（split by `,`）時仍會拿到 4 個欄位，第 3 個是 `""`。Python 端若用固定 index 取值沒問題，但若用「非空才算有效欄位」的邏輯會誤判位移。

### STATUS — 狀態查詢回應

對 `STATUS;` 指令的回應，也是 GatewayManager 健康監控心跳（`health_check_interval`）拿來判斷 `GW_OFFLINE` 用的格式：

`STATUS:<STATE>,<connected>/<target>;`

| 欄位 | 型別 | 說明 |
|---|---|---|
| STATE | enum string | `IDLE` / `SCAN` / `CONN` / `REPORT` |
| connected | int | 目前實際已連線設備數 |
| target | int | 目標設備數，來自 `CONNECT` 指令參數或開機還原，範圍 0–3 |

實際 UART 範例：
```text
STATUS:REPORT,2/2;
STATUS:IDLE,0/0;
STATUS:CONN,1/2;
```

STATE 語意對照：

| STATE | 意義 |
|---|---|
| IDLE | 閒置，等待指令，尚未開始掃描或連線 |
| SCAN | 掃描中，此時會有 `DEVICE:` 訊息陸續流出 |
| CONN | 連線建立中（GATT 服務發現 / 訂閱進行中，尚未完成） |
| REPORT | 所有目標皆已連線，進入固定週期 FTMS 資料回報（即前一節的 `FTMS:` 訊息） |

### OK — 各指令成功回應

> **警告：** 協定裡沒有一個統一、獨立的 `OK;` 訊息。每個指令成功時回的是**自己的前綴 + `:OK;`**（`VERSION;` 例外，直接回版本號，沒有額外的 OK）。Parser 判斷某條指令是否成功，要比對「該指令名稱前綴 + `:OK;`」，不能只認裸字串 `OK;`。

| 送出指令 | 成功回應 | 備註 |
|---|---|---|
| `PING;` | `BOOT:HAS_LIST,...;` 或 `BOOT:NO_LIST;` | 本身即資料回應，見上方 BOOT |
| `SCAN:START;` | `SCAN:OK;` | 已在掃描中重複送出一樣回 OK（idempotent，不會報錯） |
| `SCAN:STOP;` | `SCAN:OK;` | |
| `CONNECT:MAC1[,MAC2[,MAC3]];` | `CONNECT:OK;` | **整批設定**：會先斷開所有現有連線再依這次的清單重連，之後才會陸續進入 CONN → REPORT 狀態，OK 只代表指令被接受。**v1.3.2 起**：同一批次內的重複 MAC 解析時會自動跳過、不重複寫入目標清單（只印板端 log，不回 ERROR），Pi 端不需要自己先去重 |
| `CONNECT_ADD:MAC;` | `CONNECT_ADD:OK;` | v1.3.0 新增，**增量新增**單一設備，不影響現有連線（見下方專屬 ERROR 與注意事項） |
| `DISCONNECT:ALL;` / `DISCONNECT:MAC;` | `DISCONNECT:OK;` | |
| `REPORT:<ms>;` | `REPORT:OK;` | |
| `RSSI:<val>;` | `RSSI:OK;` | **2026-07-09 起已持久化至 NVS**，重開機後仍套用最後一次設定的門檻值（先前版本重開機會悄悄恢復成舊值，已修復） |
| `WEIGHT:<MAC>,<kg>;` | `WEIGHT:OK;` | 設定 Delightech 設備使用者體重，只存記憶體不寫 NVS，未設定時預設 70kg |
| `STATUS;` | `STATUS:<STATE>,<c>/<t>;` | 無獨立 OK，本身就是資料回應 |
| `VERSION;` | `VERSION:<fw>;` | 例：`VERSION:1.3.2;`；無獨立 OK |
| `REBOOT;` | `REBOOT:OK;` | 回應送出後約 100ms 冷重開機，之後短暫斷線屬正常現象，不應觸發 `RECONNECT_FAILED` |

### ERROR — 錯誤回應逐種列舉

分兩層：**協定層級**（整行指令格式不合法時直接攔截，任何指令都可能觸發）與**指令層級**（各指令自己驗證參數後回傳）。

**協定層級**

| 格式 | 觸發條件 |
|---|---|
| `ERROR:MISSING_SEMICOLON;` | 整行找不到結尾 `;` |
| `ERROR:UNKNOWN_CMD:<name>;` | 指令名稱不在支援清單（`PING/SCAN/CONNECT/CONNECT_ADD/DISCONNECT/REPORT/RSSI/WEIGHT/STATUS/VERSION/REBOOT`）內 |

**指令層級**

| 指令 | 格式 | 觸發條件 |
|---|---|---|
| SCAN | `SCAN:ERROR:UNKNOWN_PARAM;` | 參數不是 `START` 或 `STOP` |
| SCAN | `SCAN:ERROR:<errno>;` | 底層掃描啟動失敗，回傳系統錯誤碼 |
| CONNECT | `CONNECT:ERROR:NO_MAC;` | 完全沒帶參數 |
| CONNECT | `CONNECT:ERROR:BAD_MAC:<token>;` | 其中一個 MAC 格式不合法（非 `AA:BB:CC:DD:EE:FF`），`<token>` 回傳原始不合法字串 |
| CONNECT | `CONNECT:ERROR:NO_VALID_MAC;` | 逗號分割後沒有任何合法 MAC |
| CONNECT_ADD | `CONNECT_ADD:ERROR:NO_MAC;` | 完全沒帶參數 |
| CONNECT_ADD | `CONNECT_ADD:ERROR:BAD_MAC;` | MAC 格式不合法 |
| CONNECT_ADD | `CONNECT_ADD:ERROR:FULL;` | 已達 3 台上限（`MAX_BLE_CONN_TARGETS`），需先 `DISCONNECT:MAC;` 騰出空位 |
| CONNECT_ADD | `CONNECT_ADD:ERROR:ALREADY_EXISTS;` | 該 MAC 已經在連線目標清單中（不論是否已連線成功） |
| DISCONNECT | `DISCONNECT:ERROR:BAD_MAC;` | 單一 MAC 斷線時該 MAC 格式不合法 |
| DISCONNECT | `DISCONNECT:ERROR:<errno>;` | 底層斷線失敗，回傳系統錯誤碼 |
| REPORT | `REPORT:ERROR:BAD_VALUE;` | 參數不是正整數 |
| REPORT | `REPORT:ERROR:OUT_OF_RANGE;` | 數值超出可設定範圍（100–10000 ms） |
| RSSI | `RSSI:ERROR:BAD_VALUE;` | 參數不是合法整數 |
| WEIGHT | `WEIGHT:ERROR:BAD_PARAM;` | 找不到 `,` 分隔符 |
| WEIGHT | `WEIGHT:ERROR:BAD_MAC;` | MAC 格式不合法 |
| WEIGHT | `WEIGHT:ERROR:BAD_VALUE;` | 體重不是 1–255 的整數 |

> **注意：** `CONNECT` 最多接受 3 個 MAC，第 4 個以後的 token 會被**靜默忽略**，不會回 ERROR。這與 GatewayManager 端邏輯的 `MAX_CONN_EXCEEDED`（Pi 主動偵測「分配超過 3 台」並截斷、發警告）是兩個獨立機制——板端本身不會為超額 MAC 報錯，Pi 端組 `CONNECT:` 指令前務必自行確認 `target_macs` 長度 ≤ 3。**`CONNECT_ADD` 則相反**：超過上限或重複加入都會明確回 `ERROR:FULL;`/`ERROR:ALREADY_EXISTS;`，不會靜默忽略。**兩個條件同時成立時（清單已滿、且這個 MAC 剛好已經在清單裡）板端會優先回 `ERROR:ALREADY_EXISTS;`，不是 `ERROR:FULL;`**——因為這個 MAC 本來就在清單裡，不需要騰位子，`ALREADY_EXISTS` 是更準確的診斷（此優先順序為實機測試後修正，Pi 端解析 `CONNECT_ADD` 錯誤時應以此為準）。
>
> **`CONNECT` vs `CONNECT_ADD` 選用時機**：一開始就知道要連幾台、要連哪幾台 → 用 `CONNECT:`（會斷開所有現有連線再整批重連，即使清單裡包含已連線的 MAC 也會被斷線重連一次）。已經在 REPORT 狀態、之後才發現要多連一台、且不想讓正在回報的既有設備中斷 → 用 `CONNECT_ADD:`（完全不會影響現有連線的 GATT session）。單台移除則直接用既有的 `DISCONNECT:MAC;` 即可，不影響其他連線。

### Python 端最小解析範例（BOOT / DEVICE / STATUS / OK / ERROR）

```python
# gateway_client/protocol.py — 解析非 FTMS 控制訊息
import re

_BOOT_RE   = re.compile(r"^BOOT:(HAS_LIST,count=(\d+)|NO_LIST)$")
_DEVICE_RE = re.compile(r"^DEVICE:([0-9A-Fa-f:]+),(-?\d+),([^,]*),(\w+)$")
_STATUS_RE = re.compile(r"^STATUS:(\w+),(\d+)/(\d+)$")
_ERROR_RE  = re.compile(r"^ERROR:|.*:ERROR:")

def parse_control_line(line: str) -> dict | None:
    """解析一行非 FTMS 控制訊息（BOOT/DEVICE/STATUS/OK/ERROR）。"""
    line = line.strip().rstrip(";")

    if m := _BOOT_RE.match(line):
        has_list = m.group(1).startswith("HAS_LIST")
        return {"kind": "BOOT", "has_list": has_list,
                "count": int(m.group(2)) if has_list else 0}

    if m := _DEVICE_RE.match(line):
        mac, rssi, name, dtype = m.groups()
        return {"kind": "DEVICE", "mac": mac, "rssi": int(rssi),
                "name": name, "type": dtype}   # name 可能是 ""

    if m := _STATUS_RE.match(line):
        state, connected, target = m.groups()
        return {"kind": "STATUS", "state": state,
                "connected": int(connected), "target": int(target)}

    if _ERROR_RE.match(line):
        return {"kind": "ERROR", "raw": line}

    if line.endswith(":OK") or line.startswith("VERSION:"):
        return {"kind": "OK", "raw": line}

    return None  # 不認得的行，交給 parse_ftms_line() 再試一次
```

---

## config.py 完整設計

整個系統的唯一設定入口。切換 PC / Pi 環境只需修改這一個檔案（或透過環境變數注入），其餘程式碼不動。

**config.py — 系統唯一設定入口**

```python
"""
config.py — Dual Gateway Manager 設定檔
修改這裡切換環境 / 調整設備分配 / 調整連線策略
"""
from dataclasses import dataclass, field
from typing import Optional
import os

# ── 環境自動偵測 ─────────────────────────────────────────────
# 在 Pi 上 /sys/firmware/devicetree/base/model 存在
IS_PI: bool = os.path.exists("/sys/firmware/devicetree/base/model")

# ── GatewayConfig：單一 UART + Gateway 的所有設定 ───────────
@dataclass
class GatewayConfig:
    # ─ 識別 ───────────────────────────────────────────────────
    id: str                               # "GW1" / "GW2"，日誌與警告中使用

    # ─ Serial ─────────────────────────────────────────────────
    port: str                             # serial port 名稱
    baudrate: int = 115200
    rtscts: bool = True                  # Pi GPIO 直連需開；USB轉接器通常False

    # ─ Timeout 設定 ───────────────────────────────────────────
    boot_timeout: float = 15.0           # 等待 BOOT 訊息的最長秒數
    cmd_timeout:  float = 5.0            # 一般指令等待回應的最長秒數
    reporting_timeout: float = 30.0     # 等待進入 REPORTING 狀態的最長秒數

    # ─ 設備分配 ───────────────────────────────────────────────
    target_macs: list[str] = field(default_factory=list)
    # STATIC 策略：此 GW 負責連線的 MAC 清單（順序不重要）
    # RSSI 策略：留空，由掃描結果自動分配
    # HYBRID 策略：填優先 MAC，找不到時按 RSSI 補位

    preferred_types: list[str] = field(default_factory=list)
    # RSSI/HYBRID 策略的篩選條件，例如 ["TMILL","BIKE"]
    # 留空表示接受所有類型

    max_devices: int = 3               # 韌體硬限制，不要改

    # ─ 掃描設定 ───────────────────────────────────────────────
    scan_duration: float = 10.0         # 每次掃描秒數
    scan_max_retries: int = 3           # 目標 MAC 找不到時最多重掃次數

    # ─ 回報設定 ───────────────────────────────────────────────
    report_interval_ms: int = 1000     # FTMS 回報週期（100–10000 ms）

    # ─ 健康監控 ───────────────────────────────────────────────
    health_check_interval: float = 30.0 # STATUS 心跳週期（秒）
    data_timeout: float = 10.0         # 設備資料靜默超過此秒數 → DATA_TIMEOUT 警告

    # ─ 重連設定 ───────────────────────────────────────────────
    reconnect_attempts: int = 3         # UART 斷線後最多嘗試重連次數
    reconnect_delay:   float = 5.0     # 重連間隔秒數

    # ─ 強制重掃（忽略 HAS_LIST，每次開機都重新掃描）─────────
    # 設 True 時，啟動後會先送 DISCONNECT:ALL 清空 GW 的 NVS MAC 清單，
    # 再執行掃描。否則 GW 上電後可能自動重連舊設備，干擾掃描結果。
    force_rescan: bool = False          # 測試階段設 True 方便調試；正式部署通常 False


# ── 兩個 Gateway 設定 ──────────────────────────────────────
GW1 = GatewayConfig(
    id="GW1",
    port=os.getenv("GW1_PORT", "/dev/ttyAMA0" if IS_PI else "COM3"),
    rtscts=os.getenv("GW1_RTSCTS", "1" if IS_PI else "0") == "1",
    target_macs=[
        "AA:BB:CC:DD:EE:01",   # 跑步機 #1
        "AA:BB:CC:DD:EE:02",   # 飛輪 #1
    ],
    preferred_types=["TMILL", "BIKE"],
    report_interval_ms=1000,
)

GW2 = GatewayConfig(
    id="GW2",
    port=os.getenv("GW2_PORT", "/dev/ttyAMA2" if IS_PI else "COM5"),
    rtscts=os.getenv("GW2_RTSCTS", "1" if IS_PI else "0") == "1",
    target_macs=[
        "AA:BB:CC:DD:EE:03",   # 划船機 #1
        "AA:BB:CC:DD:EE:04",   # 橢圓機 #1
    ],
    preferred_types=["ROWER", "ELLIP"],
    report_interval_ms=1000,
)

# Manager 讀取此 list，順序即 GW1 / GW2
GATEWAYS: list[GatewayConfig] = [GW1, GW2]


# ── 設備分配策略 ──────────────────────────────────────────
class ScanStrategy:
    STATIC  = "static"   # 只連 target_macs 指定的設備
    RSSI    = "rssi"     # 掃描後依 RSSI 強弱自動分配（不需填 MAC）
    HYBRID  = "hybrid"  # 優先 target_macs；找不到時依 RSSI 補位

SCAN_STRATEGY: str = ScanStrategy.RSSI     # ← 預設 RSSI；MAC 固定時改 STATIC

# RSSI / HYBRID 策略下，每個 GW 最多自動分配幾台設備
AUTO_MAX_PER_GW: int = 2

# RSSI 容差閾值（dBm）：兩塊 Central Board 實體位置相近時，
# 對同一設備量到的 RSSI 差值往往落在雜訊範圍內（±3 dBm）。
# 差值 < 此值 → 視為「訊號相同」，改用負載均衡（已分配數量）決定，
# 避免系統性地把所有設備塞給同一塊板子。
RSSI_TIE_THRESHOLD_DB: int = 5   # 建議範圍 3–8 dBm，視現場環境調整


# ── 全域行為設定 ──────────────────────────────────────────
PARALLEL_SCAN: bool = True     # True：兩個 GW 同時掃描；False：依序掃描（省 RF 干擾）
PARALLEL_BOOT: bool = True     # True：兩個 GW 同時等 BOOT

# ── 日誌設定 ──────────────────────────────────────────────
# 2026-07-02 更新：畫面顯示與檔案寫入拆分為獨立設定，詳見下方 logging_setup.py
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")   # DEBUG / INFO / WARNING（僅影響畫面顯示層級）

# 畫面是否顯示 log；不論此值為何，log 一律會寫入 LOG_DIR
LOG_SHOW_ON_SCREEN: bool = os.getenv("LOG_SHOW_ON_SCREEN", "true").lower() in ("1", "true", "yes")

# log 檔存放資料夾；固定在本檔案所在目錄下的 log/，與執行時的工作目錄無關
LOG_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")

LOG_ROTATE_HOURS: int = 1   # 每隔幾小時產生一份新檔（檔名為建立當下的日期時間）
```

> **注意：** 舊版的 `LOG_TO_FILE` / `LOG_FILE` / `LOG_ROTATE_MB` 已移除並由新機制取代——不論平台（PC / Pi），log 一律寫入 `LOG_DIR`，畫面顯示與否由 `LOG_SHOW_ON_SCREEN` 獨立控制。詳見下一節「logging_setup.py 共用日誌模組」。

---

## logging_setup.py 共用日誌模組

2026-07-02 新增。`demo_pi.py`（Pi 正式入口）與 `test_interactive.py`（PC 互動測試工具）都改為呼叫這支共用的 `setup_logging()`，取代各自重複的 logging 設定。

### 畫面 vs. log 檔：三種內容的實際落點

系統上有三種會出現在輸出裡的內容，行為並不相同——這是這次修改要解決的核心混淆點：

| 內容 | 來源 | 畫面（LOG_SHOW_ON_SCREEN=true） | log 檔 |
|---|---|---|---|
| [UART] | `client.py` 的 TX/RX 原始封包，`logger.debug(..., extra={"tag": "UART"})` | 僅 `LOG_LEVEL=DEBUG` 時顯示 | 一律寫入（file handler 固定收 DEBUG） |
| [LOG] | 一般日誌，`logger.info/warning/error`（連線狀態、警告、錯誤） | ≥ LOG_LEVEL 才顯示（預設 INFO 會顯示） | 一律寫入 |
| 解析後資料 | `test_interactive.py` 用 `print()` 印出的 FTMS 資料 | 永遠顯示（不經過 logging 系統） | 不會寫入 |

**logging_setup.py — 共用 setup_logging()**

```python
"""
logging_setup.py — 共用 logging 設定

- 畫面是否顯示由 config.LOG_SHOW_ON_SCREEN 控制。
- 不論畫面是否顯示，log 一律寫入 config.LOG_DIR，
  檔名為建立當下的日期時間，每 config.LOG_ROTATE_HOURS 小時產生一份新檔。
- 每行訊息最前面標註 [UART]（原始封包 TX/RX）或 [LOG]（一般日誌）。
"""
import logging
import logging.handlers
import os
from datetime import datetime

import config

_FMT = "[%(tag)s] %(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class TaggedFormatter(logging.Formatter):
    """未指定 tag 的紀錄視為一般日誌，補上 [LOG]；
    UART TX/RX 由呼叫端傳入 extra={"tag": "UART"}。"""
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "tag"):
            record.tag = "LOG"
        return super().format(record)


class HourlyFileHandler(logging.handlers.TimedRotatingFileHandler):
    """每次輪轉都另開一個以當下日期時間命名的新檔，
    而非沿用固定檔名再改名。"""
    def __init__(self, log_dir: str, interval_hours: int):
        os.makedirs(log_dir, exist_ok=True)
        self._log_dir = log_dir
        super().__init__(self._make_filename(), when="H", interval=interval_hours,
                          backupCount=0, encoding="utf-8")

    def _make_filename(self) -> str:
        return os.path.join(self._log_dir, datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = self._make_filename()
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(datetime.now().timestamp()))


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    if config.LOG_SHOW_ON_SCREEN:
        console = logging.StreamHandler()
        console.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
        console.setFormatter(TaggedFormatter(fmt=_FMT, datefmt=_DATEFMT))
        root.addHandler(console)

    file_handler = HourlyFileHandler(config.LOG_DIR, config.LOG_ROTATE_HOURS)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(TaggedFormatter(fmt=_FMT, datefmt=_DATEFMT))
    root.addHandler(file_handler)
```

### 呼叫端用法

**demo_pi.py / test_interactive.py**

```python
from logging_setup import setup_logging

setup_logging()
logger = logging.getLogger(__name__)
```

**gateway_client/client.py — 標註 UART 原始封包**

```python
logger.debug(f"[{self.port}] TX: {cmd.strip()}", extra={"tag": "UART"})
logger.debug(f"[{self.port}] RX: {line.strip()}", extra={"tag": "UART"})
```

> **注意：Pi 正式部署建議**：把 `LOG_SHOW_ON_SCREEN` 設為 `false`（環境變數或直接改 config.py 預設值）。此時 `setup_logging()` 完全不建立 `StreamHandler`，等於連 stdout 都不會寫，只剩檔案 handler 在背景寫 log，符合 Pi 全自動、無需畫面的運行模式。

---

## 設備分配策略

| 策略 | config 設定 | 適用場景 | 備註 |
|---|---|---|---|
| STATIC | target_macs 填寫固定 MAC | 正式部署，設備 MAC 已知固定 | 最穩定；掃描時只驗證目標 MAC 是否可見 |
| RSSI | target_macs 留空，設 preferred_types | 設備 MAC 不固定或展示場景 | Balanced Greedy 演算法；RSSI 容差內改用負載均衡，處理兩板位置相近的問題 |
| HYBRID | target_macs 填主要 MAC，preferred_types 設類型 | 主設備固定 + 偶爾替換備用機 | 優先 config MAC；config 中找不到的 slot 改用 RSSI Balanced Greedy 補位 |

### RSSI / HYBRID 模式的核心挑戰

> **警告：** 兩塊 Central Board 物理位置相近，對同一台健身機量到的 RSSI 差值通常 ≤ 3 dBm，落在 RF 量測雜訊範圍內。若單純以 RSSI 大小決定歸屬，等效為「隨機」，且可能導致所有設備都被分配給同一塊板子。需要兩項修正：① RSSI 容差閾值、② 負載均衡。

| 情境 | 舊邏輯（純 RSSI） | 新邏輯（容差 + 均衡） |
|---|---|---|
| GW1=-55, GW2=-57（差 2 dBm） | GW1 贏（>= 成立），但差異是雜訊 | 差值 < 5 dBm → 視為相同 → 看誰分配數量少 |
| GW1=-55, GW2=-55（完全相同） | GW1 贏（>= 成立）→ 所有設備偏向 GW1 | 差值 = 0 < 5 dBm → 負載均衡 → 數量少的贏；仍相同 → GW1（確定性 fallback） |
| GW1=-55, GW2=-68（差 13 dBm） | GW1 贏（訊號明顯較強，正確） | 差值 ≥ 5 dBm → 直接給 GW1（同舊邏輯，正確） |

### Balanced Greedy 分配演算法（assigner.py）

不對每台設備單獨決定歸屬，改為整批排序後依序分配，自然處理設備數不足 6 台的情況。

**gateway_manager/assigner.py**

```python
from dataclasses import dataclass, field
import config

@dataclass
class _CandidateDevice:
    mac: str
    gw1_rssi: int | None   # None 表示此 GW 未掃到
    gw2_rssi: int | None
    best_rssi: int         # max(gw1, gw2)，用於排序

class DeviceAssigner:
    def __init__(self, gw_cfgs, strategy: str):
        self._cfgs     = {c.id: c for c in gw_cfgs}
        self._strategy = strategy
        self._tie_db   = config.RSSI_TIE_THRESHOLD_DB
        self._max_per  = config.AUTO_MAX_PER_GW

    def assign(self, scan_results: dict[str, list]) -> dict[str, list[str]]:
        """
        回傳 {gw_id: [mac, ...]}，每個 GW 應連線的 MAC 清單。
        """
        if self._strategy == "static":
            return self._assign_static(scan_results)
        elif self._strategy == "rssi":
            return self._assign_balanced_greedy(scan_results, static_first={})
        else:  # hybrid
            static_hits = self._assign_static(scan_results)
            return self._assign_balanced_greedy(scan_results, static_first=static_hits)

    # ── STATIC ────────────────────────────────────────────────
    def _assign_static(self, scan_results) -> dict[str, list[str]]:
        result = {gw_id: [] for gw_id in self._cfgs}
        for gw_id, cfg in self._cfgs.items():
            found_macs = {d.mac for d in scan_results.get(gw_id, [])}
            for mac in cfg.target_macs:
                if mac in found_macs:
                    result[gw_id].append(mac)
        return result

    # ── BALANCED GREEDY ───────────────────────────────────────
    def _assign_balanced_greedy(self, scan_results, static_first) -> dict[str, list[str]]:
        # 步驟 1：建立候選設備表（合併兩個 GW 的掃描結果）
        rssi_map: dict[str, dict[str, int]] = {}  # mac → {gw_id: rssi}
        for gw_id, devices in scan_results.items():
            for d in devices:
                rssi_map.setdefault(d.mac, {})[gw_id] = d.rssi

        candidates = [
            _CandidateDevice(
                mac=mac,
                gw1_rssi=rmap.get("GW1"),
                gw2_rssi=rmap.get("GW2"),
                best_rssi=max(rmap.values()),
            )
            for mac, rmap in rssi_map.items()
        ]
        # 步驟 2：依 best_rssi 降冪排序（整體訊號最強者優先處理）
        candidates.sort(key=lambda c: c.best_rssi, reverse=True)

        # 步驟 3：已確認的靜態分配先佔位（HYBRID 用）
        assigned: dict[str, list[str]] = {gw_id: list(macs)
                                          for gw_id, macs in static_first.items()}
        assigned.setdefault("GW1", [])
        assigned.setdefault("GW2", [])
        already = {mac for macs in assigned.values() for mac in macs}

        # 步驟 4：逐一分配剩餘候選設備
        for dev in candidates:
            if dev.mac in already:
                continue  # 已被靜態佔位，跳過

            gw1_full = len(assigned["GW1"]) >= self._max_per
            gw2_full = len(assigned["GW2"]) >= self._max_per
            if gw1_full and gw2_full:
                break  # 兩邊都滿了

            # 只有一邊掃到 → 直接分配給掃到的那邊
            if dev.gw1_rssi is None and not gw2_full:
                assigned["GW2"].append(dev.mac); already.add(dev.mac); continue
            if dev.gw2_rssi is None and not gw1_full:
                assigned["GW1"].append(dev.mac); already.add(dev.mac); continue
            if dev.gw1_rssi is None: continue  # GW2 也滿了，放棄
            if dev.gw2_rssi is None: continue  # GW1 也滿了，放棄

            # 兩邊都掃到：決策優先順序
            winner = self._pick_gw(
                dev.gw1_rssi, dev.gw2_rssi,
                len(assigned["GW1"]), len(assigned["GW2"]),
                gw1_full, gw2_full,
            )
            assigned[winner].append(dev.mac)
            already.add(dev.mac)

        return assigned

    # ── 決策核心：容差 + 負載均衡 ────────────────────────────
    def _pick_gw(self, r1: int, r2: int,
                 cnt1: int, cnt2: int,
                 full1: bool, full2: bool) -> str:
        """
        決定要分配給 GW1 還是 GW2。
        優先順序：
          1. 某邊已滿 → 強制給另一邊
          2. RSSI 差值 >= 閾值 → 訊號強的贏
          3. RSSI 差值 < 閾值（容差內，視為相同）→ 數量少的贏
          4. 數量也相同 → GW1（確定性 fallback）
        """
        if full1: return "GW2"
        if full2: return "GW1"
        if abs(r1 - r2) >= self._tie_db:           # 訊號差異明顯
            return "GW1" if r1 > r2 else "GW2"
        if cnt1 != cnt2:                            # 容差內 → 負載均衡
            return "GW1" if cnt1 < cnt2 else "GW2"
        return "GW1"                               # 完全相同 → GW1 fallback
```

### 設備數不足 6 台時的分配行為

Balanced Greedy 先依最強訊號排序，再交替填入兩邊，自然達到均衡分配。

**範例：現場 3 台設備，AUTO_MAX_PER_GW = 2，RSSI_TIE_THRESHOLD_DB = 5**

```text
掃描結果（兩板都掃到所有設備）：
  Device A  GW1=-55  GW2=-57  best=-55  差=2 dBm（容差內）
  Device B  GW1=-62  GW2=-60  best=-60  差=2 dBm（容差內）
  Device C  GW1=-75  GW2=-73  best=-73  差=2 dBm（容差內）

依 best_rssi 排序後逐一處理：

  [Device A]  cnt1=0, cnt2=0 → 相同 → |−55−(−57)|=2 < 5 → 容差 → cnt 比較 → 0==0 → GW1
              → GW1:[A]  GW2:[]

  [Device B]  cnt1=1, cnt2=0 → GW2 較少（負載均衡）→ GW2
              → GW1:[A]  GW2:[B]

  [Device C]  cnt1=1, cnt2=1 → 相同 → |−75−(−73)|=2 < 5 → 容差 → cnt 比較 → 1==1 → GW1
              → GW1:[A,C]  GW2:[B]

結果：GW1 連 A, C；GW2 連 B ✓（1台差距，已達最佳均衡）
```

| 現場設備數 | GW1 | GW2 | 行為說明 |
|---|---|---|---|
| 6 台 | 3 台 | 3 台 | 滿載，均分 |
| 5 台 | 3 台 | 2 台 | 多的給 GW1（fallback） |
| 4 台 | 2 台 | 2 台 | 均分 |
| 3 台 | 2 台 | 1 台 | 多的給 GW1 |
| 2 台 | 1 台 | 1 台 | 均分 |
| 1 台 | 1 台 | 0 台 | GW2 發 `DEVICE_NOT_FOUND` WARNING，跳過 CONNECT |
| 0 台 | 0 台 | 0 台 | 兩個都發警告，觸發掃描重試流程 |

> **注意：** GW2 分配到 0 台時，GatewayManager 跳過對 GW2 的 CONNECT 指令，GW2 維持 idle 狀態。GW1 的連線流程不受影響，仍正常進入 REPORTING。

---

## GatewayManager

**gateway_manager/manager.py**

```python
import threading, logging, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .gateway_client import GatewayClient
from .models import FTMSData, DeviceInfo, StatusInfo, BootInfo
from .alerts import AlertBus, GatewayAlert, AlertType, Severity
from .assigner import DeviceAssigner
import config

class GatewayManager:
    """
    持有兩個 GatewayClient，協調掃描、連線、健康監控。
    上層只需和 GatewayManager 互動，不需直接操作 GatewayClient。
    """

    def __init__(self, gw_configs=config.GATEWAYS):
        self._cfgs   = {c.id: c for c in gw_configs}
        self._clients: dict[str, GatewayClient] = {}
        self._boots:   dict[str, BootInfo] = {}
        self._alert_bus = AlertBus()
        self._assigner  = DeviceAssigner(gw_configs, config.SCAN_STRATEGY)
        self._ftms_cbs:  list = []
        self._last_data: dict[str, float] = {}   # mac → timestamp
        self._health_thread: threading.Thread | None = None
        self._running = False

    # ── 生命週期 ────────────────────────────────────────────────
    def start(self) -> dict[str, BootInfo]:
        """初始化所有 GW，回傳 {gw_id: BootInfo}"""
        self._running = True
        if config.PARALLEL_BOOT:
            with ThreadPoolExecutor(max_workers=2) as ex:
                futs = {ex.submit(self._init_one, cfg): cfg
                        for cfg in self._cfgs.values()}
                for f in as_completed(futs):
                    gw_id, boot = f.result()
                    self._boots[gw_id] = boot
        else:
            for cfg in self._cfgs.values():
                gw_id, boot = self._init_one(cfg)
                self._boots[gw_id] = boot
        self._start_health_monitor()
        return self._boots

    def stop(self):
        self._running = False
        for c in self._clients.values():
            c.close()

    def __enter__(self): self.start(); return self
    def __exit__(self, *_): self.stop()

    # ── 掃描與連線 ───────────────────────────────────────────────
    def run_scan_and_connect(self):
        """判斷哪些 GW 需要掃描，執行，分配，連線。"""
        to_scan = [
            cfg for cfg in self._cfgs.values()
            if cfg.force_rescan or not self._boots.get(cfg.id, BootInfo(False)).has_list
        ]
        if not to_scan:
            return  # 全部 HAS_LIST，等 REPORTING 即可

        # force_rescan：先送 DISCONNECT:ALL 清空 NVS MAC 清單
        # 必須在掃描前執行，否則 GW 可能已自動重連舊設備，干擾掃描
        for cfg in to_scan:
            if cfg.force_rescan:
                client = self._clients.get(cfg.id)
                if client:
                    try:
                        client.disconnect_all()
                        logging.info(f"[{cfg.id}] DISCONNECT:ALL 完成，NVS MAC 清單已清空")
                    except Exception as e:
                        logging.warning(f"[{cfg.id}] DISCONNECT:ALL 失敗: {e}，仍繼續掃描")

        scan_results: dict[str, list[DeviceInfo]] = {}
        if config.PARALLEL_SCAN:
            with ThreadPoolExecutor(max_workers=2) as ex:
                futs = {ex.submit(self._scan_one, cfg): cfg for cfg in to_scan}
                for f in as_completed(futs):
                    gw_id, devices = f.result()
                    scan_results[gw_id] = devices
        else:
            for cfg in to_scan:
                gw_id, devices = self._scan_one(cfg)
                scan_results[gw_id] = devices

        assignments = self._assigner.assign(scan_results)
        for gw_id, macs in assignments.items():
            if not macs:
                self._alert_bus.emit(GatewayAlert(gw_id, AlertType.DEVICE_NOT_FOUND,
                    Severity.WARNING, f"{gw_id} 無法找到任何目標設備"))
                continue
            self._clients[gw_id].connect_devices(macs, wait=True)

    # ── 統一 FTMS 回調 ───────────────────────────────────────────
    def on_ftms_data(self, callback):
        """登記統一 FTMS 資料回調。收到的 FTMSData 物件會附帶 gateway_id 欄位。"""
        self._ftms_cbs.append(callback)

    def on_alert(self, callback):
        self._alert_bus.subscribe(callback)

    # ── 查詢 ─────────────────────────────────────────────────────
    def get_status_all(self) -> dict[str, StatusInfo]:
        return {gw_id: c.get_status() for gw_id, c in self._clients.items()}

    def set_report_interval_all(self, ms: int):
        for c in self._clients.values():
            c.set_report_interval(ms)

    # ── 內部：初始化單一 GW ──────────────────────────────────────
    def _init_one(self, cfg) -> tuple[str, BootInfo]:
        try:
            client = GatewayClient(cfg.port, cfg.baudrate,
                                   cfg.cmd_timeout, cfg.rtscts)
            boot = client.connect(boot_timeout=cfg.boot_timeout)
            client.on_ftms_data(lambda d, gid=cfg.id: self._dispatch_ftms(gid, d))
            client.on_error(lambda e, gid=cfg.id: self._alert_bus.emit(
                GatewayAlert(gid, AlertType.GW_ERROR, Severity.WARNING, e)))
            self._clients[cfg.id] = client
            return cfg.id, boot
        except Exception as e:
            self._alert_bus.emit(GatewayAlert(
                cfg.id, AlertType.GW_INIT_FAILED, Severity.CRITICAL, str(e)))
            return cfg.id, BootInfo(has_list=False)

    # ── 內部：掃描單一 GW（含重試）──────────────────────────────
    def _scan_one(self, cfg) -> tuple[str, list[DeviceInfo]]:
        cfg_obj = self._cfgs[cfg.id]
        client  = self._clients.get(cfg.id)
        if not client: return cfg.id, []
        for attempt in range(cfg_obj.scan_max_retries):
            devices = client.scan(cfg_obj.scan_duration)
            if devices: return cfg.id, devices
            logging.warning(f"[{cfg.id}] 掃描空結果 ({attempt+1}/{cfg_obj.scan_max_retries})")
        self._alert_bus.emit(GatewayAlert(
            cfg.id, AlertType.SCAN_EMPTY, Severity.WARNING,
            f"掃描 {cfg_obj.scan_max_retries} 次仍無設備"))
        return cfg.id, []

    # ── 內部：FTMS 分派 ──────────────────────────────────────────
    def _dispatch_ftms(self, gw_id: str, data: FTMSData):
        self._last_data[data.mac] = time.monotonic()
        tagged = TaggedFTMSData(gateway_id=gw_id, **vars(data))
        for cb in self._ftms_cbs:
            cb(tagged)

    # ── 健康監控 ─────────────────────────────────────────────────
    def _start_health_monitor(self):
        interval = min(c.health_check_interval for c in self._cfgs.values())
        def loop():
            while self._running:
                time.sleep(interval)
                self._health_check()
        self._health_thread = threading.Thread(target=loop, daemon=True)
        self._health_thread.start()

    def _health_check(self):
        for gw_id, client in self._clients.items():
            try:
                client.get_status()
            except Exception:
                self._alert_bus.emit(GatewayAlert(
                    gw_id, AlertType.GW_OFFLINE, Severity.CRITICAL,
                    f"{gw_id} STATUS 無回應"))
        # 資料逾時檢查
        now = time.monotonic()
        for gw_id, cfg in self._cfgs.items():
            for mac in cfg.target_macs:
                last = self._last_data.get(mac, 0)
                if last > 0 and now - last > cfg.data_timeout:
                    self._alert_bus.emit(GatewayAlert(
                        gw_id, AlertType.DATA_TIMEOUT, Severity.WARNING,
                        f"{mac} 超過 {cfg.data_timeout}s 無 FTMS 資料"))
```

---

## AlertBus — 警告系統

**gateway_manager/alerts.py**

```python
from dataclasses import dataclass, field
import time

class AlertType:
    GW_INIT_FAILED  = "GW_INIT_FAILED"    # GW 開機無回應
    GW_OFFLINE      = "GW_OFFLINE"        # STATUS 心跳失敗
    GW_ERROR        = "GW_ERROR"          # GW 回傳 ERROR 訊息
    BOOT_UNEXPECTED = "BOOT_UNEXPECTED"   # 運行中收到 BOOT（GW 重啟）
    DEVICE_NOT_FOUND= "DEVICE_NOT_FOUND"  # 掃描完仍找不到目標 MAC
    DUPLICATE_DEVICE= "DUPLICATE_DEVICE"  # 同一 MAC 出現在兩個 GW 掃描結果
    MAX_CONN_EXCEEDED="MAX_CONN_EXCEEDED" # 指定 MAC 數超過 GW 上限 3
    SCAN_EMPTY      = "SCAN_EMPTY"        # 掃描 max_retries 次仍無設備
    DATA_TIMEOUT    = "DATA_TIMEOUT"      # 設備超過 data_timeout 無資料
    RECONNECT_FAILED= "RECONNECT_FAILED"  # UART 重連 reconnect_attempts 次失敗

class Severity:
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"

@dataclass
class GatewayAlert:
    gateway_id:  str
    alert_type:  str
    severity:    str
    message:     str
    timestamp:   float = field(default_factory=time.time)

class AlertBus:
    def __init__(self):
        self._subs = []

    def subscribe(self, cb): self._subs.append(cb)

    def emit(self, alert: GatewayAlert):
        logging.log(
            logging.CRITICAL if alert.severity == "CRITICAL" else logging.WARNING,
            f"[ALERT][{alert.gateway_id}][{alert.alert_type}] {alert.message}"
        )
        for cb in self._subs:
            cb(alert)
```

---

## 問題處理策略

| 問題類型 | 嚴重度 | 觸發條件 | 處理方式 |
|---|---|---|---|
| GW_INIT_FAILED | **CRITICAL** | 開機後 boot_timeout 內無 BOOT 訊息 | 發出警告，該 GW 跳過，另一 GW 正常運行；可設定重試 |
| BOOT_UNEXPECTED | **CRITICAL** | 正常運行中收到 BOOT（GW 韌體重啟） | 對該 GW 重走初始化→掃描→連線流程，另一 GW 不中斷 |
| GW_OFFLINE | **CRITICAL** | STATUS 心跳連續失敗 | 嘗試 UART 重連 reconnect_attempts 次；仍失敗 → RECONNECT_FAILED |
| RECONNECT_FAILED | **CRITICAL** | UART 重連超過上限次數 | 發出 CRITICAL 警告，停止該 GW；上層決定是否停機或繼續 |
| DEVICE_NOT_FOUND | WARNING | scan_max_retries 次後仍找不到目標 MAC | 發出警告，略過未找到的 MAC，連線剩餘找到的設備 |
| SCAN_EMPTY | WARNING | 重試後掃描結果始終為空 | 發出警告；若 HYBRID/RSSI 策略 → 略過此 GW 的連線步驟 |
| DUPLICATE_DEVICE | INFO | 同一 MAC 出現在兩個 GW 的掃描結果 | config 靜態優先 → 否則 RSSI 高者取得；發出 INFO 記錄解決方式 |
| MAX_CONN_EXCEEDED | WARNING | 分配到該 GW 的 MAC 超過 3 台 | 截斷至 3 台（依 RSSI 或 config 順序取前 3），發出 WARNING 列出丟棄的 MAC |
| DATA_TIMEOUT | WARNING | 已連線設備超過 data_timeout 無 FTMS 資料 | 發出 WARNING（含 MAC + 靜默時間）；不自動斷線，讓上層決定 |

---

## 目錄結構（更新後）

```text
gateway_manager/                  # 新增：雙 GW 協調層
│
├── __init__.py                   # re-export: GatewayManager, GatewayAlert
├── manager.py                    # GatewayManager 主類
├── assigner.py                   # DeviceAssigner：STATIC / RSSI / HYBRID
└── alerts.py                     # AlertBus, GatewayAlert, AlertType, Severity

gateway_client/                   # 原有，不修改
├── __init__.py
├── models.py                     # + TaggedFTMSData (新增 gateway_id 欄位)
├── protocol.py
├── exceptions.py
└── client.py

config.py                         # ← 唯一需要修改的檔案（設備 MAC、port、策略）
logging_setup.py                  # 新增：共用 setup_logging()，畫面/檔案日誌統一設定
log/                              # 新增：setup_logging() 自動建立，每小時一份新檔（不進版控）

demo_pi.py                        # Pi 正式運行入口
test_interactive.py               # PC 測試 CLI（單一 GW）
test_dual_interactive.py          # PC 測試 CLI（雙 GW）
```

**demo_pi.py — Pi 運行入口（完整範例）**

```python
from gateway_manager import GatewayManager, GatewayAlert
from gateway_client.models import TaggedFTMSData
from logging_setup import setup_logging
import signal, logging, config

setup_logging()   # 畫面顯示由 config.LOG_SHOW_ON_SCREEN 控制；log 一律寫入 config.LOG_DIR

def handle_ftms(data: TaggedFTMSData):
    # data.gateway_id 可以知道來自哪個 GW
    logging.debug(f"[{data.gateway_id}][{data.device_type}] {data.mac} "
                  f"speed={data.instantaneous_speed} power={data.instantaneous_power}W")
    # ← 這裡接資料庫寫入 / MQTT publish / WebSocket 推送

def handle_alert(alert: GatewayAlert):
    logging.warning(f"[ALERT][{alert.severity}][{alert.gateway_id}] "
                    f"{alert.alert_type}: {alert.message}")
    if alert.severity == "CRITICAL":
        # ← 這裡接系統通知 / LINE Notify / 警鈴
        pass

with GatewayManager() as mgr:
    mgr.on_ftms_data(handle_ftms)
    mgr.on_alert(handle_alert)

    mgr.run_scan_and_connect()      # 掃描→分配→連線（含重試）
    mgr.set_report_interval_all(1000)

    logging.info("系統就緒，開始接收 FTMS 資料")
    signal.pause()                  # 等待 SIGTERM / SIGINT（systemd 管理）
```

---

## Pi 適配要點

| 項目 | PC 測試 | Pi 正式 |
|---|---|---|
| GW1 port | COM3（環境變數 GW1_PORT） | /dev/ttyAMA0 |
| GW2 port | COM5（環境變數 GW2_PORT） | /dev/ttyAMA2（或 ttyAMA4） |
| rtscts | False（USB 轉接器） | True（GPIO 直連） |
| Pi UART 準備 | N/A | raspi-config → 啟用 UART × 2，停用 Serial Console |
| BT 占用 | N/A | /boot/config.txt 加 dtoverlay=disable-bt（釋出 ttyAMA0） |
| 第二條 UART | N/A | /boot/config.txt 加 dtoverlay=uart2（啟用 ttyAMA2） |
| 服務管理 | 直接執行 | systemd service：Restart=on-failure，標準輸出導向 journald |
| 日誌 | log/ 資料夾，每小時新檔；畫面預設顯示（LOG_SHOW_ON_SCREEN=true） | 同一套機制；建議設 LOG_SHOW_ON_SCREEN=false 關閉畫面輸出 |
| config.py 修改 | config.py 僅需填入正確 MAC 和 port；IS_PI 自動偵測其他參數（橫跨兩欄） | ↑ |

> **重要：** 從 PC 移植到 Pi 只需：①確認 Pi UART 硬體設定 ②在 config.py 填入真實設備 MAC ③設定環境變數或直接修改 port 字串。程式邏輯**零修改**。

**Pi systemd service 範例**

```text
# /etc/systemd/system/gateway.service
[Unit]
Description=BLE Central Board Manager
After=network.target

[Service]
ExecStart=/home/pi/venv/bin/python /home/pi/gateway/demo_pi.py
WorkingDirectory=/home/pi/gateway
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target

# 啟用：
# sudo systemctl enable gateway && sudo systemctl start gateway
# 查看日誌：sudo journalctl -u gateway -f
```
