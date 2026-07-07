"""
demo_pi.py — Pi 正式部署入口

用法（Pi 上執行）：
  python demo_pi.py

環境變數覆寫範例：
  GW1_PORT=/dev/ttyAMA0 GW2_PORT=/dev/ttyAMA2 LOG_LEVEL=DEBUG python demo_pi.py
"""
import logging
import signal
import sys

import config
from logging_setup import setup_logging
from gateway_client.models import TaggedFTMSData
from gateway_manager import GatewayManager, GatewayAlert, AlertType, Severity

logger = logging.getLogger(__name__)


# ── FTMS 資料處理 ─────────────────────────────────────────────────
def handle_ftms(data: TaggedFTMSData) -> None:
    """
    每次收到 FTMS 資料時呼叫。
    data.gateway_id 可知來自 GW1 / GW2；data.mac 可知來自哪台設備。
    此處僅示範 log；正式整合時改為寫入 DB / 傳送 MQTT 等。
    """
    speed = data.instantaneous_speed
    power = data.instantaneous_power
    rate  = data.stroke_rate
    dist  = data.total_distance
    logger.info(
        f"[{data.gateway_id}] {data.mac} ({data.device_type}) | "
        f"speed={speed} km/h  power={power} W  rate={rate}  dist={dist} m"
    )


# ── 警告處理 ──────────────────────────────────────────────────────
def handle_alert(alert: GatewayAlert) -> None:
    """
    接收來自 AlertBus 的警告。
    Severity.CRITICAL → error log；其餘 → warning log。
    正式部署可改為發送 LINE Notify / Slack / 寫入 DB。
    """
    msg = (
        f"[{alert.gateway_id}] [{alert.alert_type}] "
        f"({alert.severity}) {alert.message}"
    )
    if alert.severity == Severity.CRITICAL:
        logger.error(msg)
    else:
        logger.warning(msg)


# ── 主程式 ────────────────────────────────────────────────────────
def main() -> None:
    setup_logging()
    logger.info("=== BLE Central Board Gateway Manager 啟動 ===")
    logger.info(f"平台: {'Raspberry Pi' if config.IS_PI else 'PC / 開發機'}")
    logger.info(f"GW1 port: {config.GW1.port}  GW2 port: {config.GW2.port}")
    logger.info(f"策略: {config.SCAN_STRATEGY}")

    manager = GatewayManager()
    manager.on_ftms_data(handle_ftms)
    manager.on_alert(handle_alert)

    # 優雅關閉：Ctrl+C 或 SIGTERM
    def _shutdown(signum, frame):
        logger.info("收到關閉訊號，正在停止 GatewayManager...")
        manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 初始化（等 BOOT）
    boots = manager.start()
    for gw_id, boot in boots.items():
        logger.info(
            f"[{gw_id}] BOOT: has_list={boot.has_list}, count={boot.count}"
        )

    # 掃描 + 設備分配 + 連線
    manager.run_scan_and_connect()

    # 等所有板進入 REPORTING（最多 30 秒）
    manager.wait_all_reporting(timeout=30.0)
    logger.info("所有 Central Board 已進入 REPORTING，開始持續接收資料...")

    # 主 thread 在此阻塞，等待 SIGTERM / SIGINT
    signal.pause()


if __name__ == "__main__":
    main()
