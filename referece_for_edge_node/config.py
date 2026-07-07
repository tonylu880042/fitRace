"""
config.py — Dual Central Board Manager 設定檔

切換 PC / Pi 環境、調整設備 MAC 分配、調整連線策略，
只需修改這一個檔案，其餘程式碼不動。
"""
from dataclasses import dataclass, field
import os

# ── 環境自動偵測 ──────────────────────────────────────────────
# Pi 上 /sys/firmware/devicetree/base/model 存在
IS_PI: bool = os.path.exists("/sys/firmware/devicetree/base/model")


# ── GatewayConfig：單一 UART + Central Board 的所有設定 ───────
@dataclass
class GatewayConfig:
    # ─ 識別 ─────────────────────────────────────────────────
    id: str                              # "GW1" / "GW2"，日誌與警告中使用

    # ─ Serial ───────────────────────────────────────────────
    port: str                            # serial port 名稱
    baudrate: int = 115200
    rtscts: bool = True                  # Pi GPIO 直連需開；USB 轉接器通常 False

    # ─ Timeout 設定 ─────────────────────────────────────────
    boot_timeout: float = 15.0           # 等待 BOOT 訊息的最長秒數
    cmd_timeout: float = 5.0             # 一般指令等待回應的最長秒數
    reporting_timeout: float = 30.0      # 等待進入 REPORTING 狀態的最長秒數

    # ─ 設備分配 ─────────────────────────────────────────────
    target_macs: list = field(default_factory=list)
    # STATIC 策略：此板負責連線的 MAC 清單（順序不重要）
    # RSSI 策略：留空，由掃描結果自動分配
    # HYBRID 策略：填優先 MAC，找不到時按 RSSI 補位

    preferred_types: list = field(default_factory=list)
    # RSSI / HYBRID 策略的類型篩選，例如 ["TMILL", "BIKE"]
    # 留空表示接受所有類型

    max_devices: int = 3                 # 韌體硬限制，請勿修改

    # ─ 掃描設定 ─────────────────────────────────────────────
    scan_duration: float = 10.0          # 每次掃描秒數
    scan_max_retries: int = 3            # 目標 MAC 找不到時最多重掃次數

    # ─ 回報設定 ─────────────────────────────────────────────
    report_interval_ms: int = 1000       # FTMS 回報週期（100–10000 ms）

    # ─ 健康監控 ─────────────────────────────────────────────
    health_check_interval: float = 30.0  # STATUS 心跳週期（秒）
    data_timeout: float = 10.0           # 設備資料靜默超過此秒數 → DATA_TIMEOUT 警告

    # ─ 重連設定 ─────────────────────────────────────────────
    reconnect_attempts: int = 3          # UART 斷線後最多嘗試重連次數
    reconnect_delay: float = 5.0         # 重連間隔秒數

    # ─ 強制重掃 ─────────────────────────────────────────────
    # 設 True 時，啟動後先送 DISCONNECT:ALL 清空 NVS MAC 清單，再執行掃描。
    # 否則板子上電後可能自動重連舊設備，干擾掃描結果。
    force_rescan: bool = False           # 測試階段設 True；正式部署通常 False


# ── 兩塊 Central Board 設定 ──────────────────────────────────
GW1 = GatewayConfig(
    id="GW1",
    port=os.getenv("GW1_PORT", "/dev/ttyAMA0" if IS_PI else "COM3"),
    rtscts=os.getenv("GW1_RTSCTS", "1" if IS_PI else "0") == "1",
    target_macs=[
        "AA:BB:CC:DD:EE:01",   # 跑步機 #1  ← 修改為實際 MAC
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
        "AA:BB:CC:DD:EE:03",   # 划船機 #1  ← 修改為實際 MAC
        "AA:BB:CC:DD:EE:04",   # 橢圓機 #1
    ],
    preferred_types=["ROWER", "ELLIP"],
    report_interval_ms=1000,
)

# Manager 讀取此 list，順序即 GW1 / GW2
GATEWAYS: list = [GW1, GW2]


# ── 設備分配策略 ──────────────────────────────────────────────
class ScanStrategy:
    STATIC = "static"   # 只連 target_macs 指定的設備（正式部署建議）
    RSSI   = "rssi"     # 掃描後依 Balanced Greedy 自動分配（不需填 MAC）
    HYBRID = "hybrid"   # 優先 target_macs；找不到時依 RSSI 補位

SCAN_STRATEGY: str = ScanStrategy.RSSI

# RSSI / HYBRID 策略下，每個板最多自動分配幾台設備（≤ max_devices）
AUTO_MAX_PER_GW: int = 2

# RSSI 容差閾值（dBm）：兩塊板實體位置相近，量到的 RSSI 差值常落在雜訊範圍。
# 差值 < 此值 → 視為「訊號相同」，改用負載均衡（已分配數量）決定，
# 避免系統性地把所有設備塞給同一塊板子。
RSSI_TIE_THRESHOLD_DB: int = 5   # 建議範圍 3–8 dBm，視現場環境調整


# ── 全域行為設定 ──────────────────────────────────────────────
PARALLEL_SCAN: bool = True   # True：兩塊板同時掃描；False：依序掃描
PARALLEL_BOOT: bool = True   # True：兩塊板同時等 BOOT

# ── 日誌設定 ──────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")   # DEBUG / INFO / WARNING（僅影響畫面顯示層級）

# 畫面是否顯示 log；不論此值為何，log 一律會寫入 LOG_DIR
LOG_SHOW_ON_SCREEN: bool = os.getenv("LOG_SHOW_ON_SCREEN", "true").lower() in ("1", "true", "yes")

# log 檔存放資料夾；固定在本檔案所在目錄下的 log/，與執行時的工作目錄無關
LOG_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")

LOG_ROTATE_HOURS: int = 1   # 每隔幾小時產生一份新檔（檔名為建立當下的日期時間）
