import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class AlertType:
    GW_INIT_FAILED    = "GW_INIT_FAILED"     # 開機後 boot_timeout 內無 BOOT 訊息
    GW_OFFLINE        = "GW_OFFLINE"         # STATUS 心跳連續失敗
    GW_ERROR          = "GW_ERROR"           # Central Board 回傳 ERROR 訊息
    BOOT_UNEXPECTED   = "BOOT_UNEXPECTED"    # 正常運行中收到 BOOT（板子重啟）
    DEVICE_NOT_FOUND  = "DEVICE_NOT_FOUND"   # 掃描後仍找不到任何目標設備
    DUPLICATE_DEVICE  = "DUPLICATE_DEVICE"   # 同一 MAC 出現在兩塊板子的掃描結果（INFO）
    MAX_CONN_EXCEEDED = "MAX_CONN_EXCEEDED"  # 分配到該板的 MAC 超過韌體上限 3
    SCAN_EMPTY        = "SCAN_EMPTY"         # 重試後掃描結果始終為空
    DATA_TIMEOUT      = "DATA_TIMEOUT"       # 已連線設備超過 data_timeout 無 FTMS 資料
    RECONNECT_FAILED  = "RECONNECT_FAILED"   # UART 重連超過上限次數


class Severity:
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class GatewayAlert:
    gateway_id: str
    alert_type: str
    severity: str
    message: str
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return f"[{self.severity}][{self.gateway_id}][{self.alert_type}] {self.message}"


class AlertBus:
    def __init__(self):
        self._subscribers: list = []

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def emit(self, alert: GatewayAlert) -> None:
        level = logging.CRITICAL if alert.severity == Severity.CRITICAL else logging.WARNING
        logger.log(level, str(alert))
        for cb in self._subscribers:
            try:
                cb(alert)
            except Exception as e:
                logger.warning(f"AlertBus subscriber 例外: {e}")
