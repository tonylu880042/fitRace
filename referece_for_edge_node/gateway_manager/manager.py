"""
manager.py — GatewayManager

持有兩個 GatewayClient，協調：
  - 平行 BOOT 初始化
  - force_rescan 時先 DISCONNECT:ALL 清空 NVS
  - 平行掃描（含重試）
  - Balanced Greedy 設備分配
  - CONNECT + 等待 REPORTING
  - 統一 FTMS 資料流（附加 gateway_id）
  - 健康監控（STATUS 心跳 + 資料逾時偵測）
  - 非預期 BOOT 處理（板子重啟時自動重走初始化→掃描→連線）
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import config
from gateway_client.client import GatewayClient
from gateway_client.models import BootInfo, FTMSData, TaggedFTMSData, StatusInfo
from gateway_client.exceptions import GatewayError, GatewayTimeoutError

from .alerts import AlertBus, GatewayAlert, AlertType, Severity
from .assigner import DeviceAssigner

logger = logging.getLogger(__name__)


class GatewayManager:
    """
    管理多個 BLE Central Board，提供統一的掃描、連線、資料接收與健康監控介面。
    上層程式只需和 GatewayManager 互動，不直接操作 GatewayClient。
    """

    def __init__(self, gw_configs: Optional[list] = None):
        if gw_configs is None:
            gw_configs = config.GATEWAYS
        self._cfgs     = {c.id: c for c in gw_configs}
        self._clients: dict  = {}
        self._boots:   dict  = {}
        self._alert_bus      = AlertBus()
        self._assigner       = DeviceAssigner(gw_configs, config.SCAN_STRATEGY)
        self._ftms_cbs: list = []
        self._last_data: dict = {}      # mac → monotonic timestamp
        self._health_thread: Optional[threading.Thread] = None
        self._running = False

    # ── Context manager ──────────────────────────────────────────
    def __enter__(self) -> "GatewayManager":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ── Lifecycle ────────────────────────────────────────────────
    def start(self) -> dict:
        """
        初始化所有 Central Board，回傳 {gw_id: BootInfo}。
        失敗的 GW 發出 GW_INIT_FAILED CRITICAL 警告，其他 GW 繼續運行。
        """
        self._running = True
        if config.PARALLEL_BOOT:
            with ThreadPoolExecutor(max_workers=len(self._cfgs)) as ex:
                futs = {
                    ex.submit(self._init_one, cfg): cfg
                    for cfg in self._cfgs.values()
                }
                for f in as_completed(futs):
                    gw_id, boot = f.result()
                    self._boots[gw_id] = boot
        else:
            for cfg in self._cfgs.values():
                gw_id, boot = self._init_one(cfg)
                self._boots[gw_id] = boot

        self._start_health_monitor()
        return self._boots

    def stop(self) -> None:
        """停止健康監控，關閉所有 Central Board 連線。"""
        self._running = False
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass

    # ── Scan & Connect ───────────────────────────────────────────
    def run_scan_and_connect(self) -> None:
        """
        判斷哪些 Central Board 需要掃描，執行分配，發出 CONNECT。
        包含 force_rescan 前置 DISCONNECT:ALL、掃描重試、設備數量超限處理。
        """
        to_scan = [
            cfg for cfg in self._cfgs.values()
            if cfg.force_rescan
            or not self._boots.get(cfg.id, BootInfo(False)).has_list
        ]

        if not to_scan:
            logger.info("所有 Central Board 均 HAS_LIST，跳過掃描，等待 REPORTING")
            return

        # force_rescan：先送 DISCONNECT:ALL 清空 NVS MAC 清單，再掃描
        # 若不清空，板子可能已自動重連舊設備，干擾掃描結果
        for cfg in to_scan:
            if cfg.force_rescan:
                client = self._clients.get(cfg.id)
                if client:
                    try:
                        client.disconnect_all()
                        logger.info(f"[{cfg.id}] DISCONNECT:ALL 完成，NVS MAC 清單已清空")
                    except Exception as e:
                        logger.warning(f"[{cfg.id}] DISCONNECT:ALL 失敗: {e}，仍繼續掃描")

        # 執行掃描
        scan_results: dict = {}
        if config.PARALLEL_SCAN:
            with ThreadPoolExecutor(max_workers=len(to_scan)) as ex:
                futs = {ex.submit(self._scan_one, cfg): cfg for cfg in to_scan}
                for f in as_completed(futs):
                    gw_id, devices = f.result()
                    scan_results[gw_id] = devices
        else:
            for cfg in to_scan:
                gw_id, devices = self._scan_one(cfg)
                scan_results[gw_id] = devices

        # 設備分配
        assignments = self._assigner.assign(scan_results)
        logger.info(f"設備分配結果: { {k: v for k, v in assignments.items()} }")

        # 發出 CONNECT
        for gw_id, macs in assignments.items():
            if not macs:
                self._alert_bus.emit(GatewayAlert(
                    gw_id, AlertType.DEVICE_NOT_FOUND, Severity.WARNING,
                    f"{gw_id} 無法找到任何目標設備，跳過 CONNECT"
                ))
                continue

            cfg = self._cfgs[gw_id]
            if len(macs) > cfg.max_devices:
                dropped = macs[cfg.max_devices:]
                macs = macs[:cfg.max_devices]
                self._alert_bus.emit(GatewayAlert(
                    gw_id, AlertType.MAX_CONN_EXCEEDED, Severity.WARNING,
                    f"分配設備數超過上限 {cfg.max_devices}，丟棄: {dropped}"
                ))

            client = self._clients.get(gw_id)
            if client:
                try:
                    client.connect_devices(macs, wait=False)
                    logger.info(f"[{gw_id}] 已送出 CONNECT → {macs}")
                except Exception as e:
                    logger.error(f"[{gw_id}] connect_devices 失敗: {e}")

    def wait_all_reporting(self, timeout: float = 30.0) -> None:
        """等待所有有發出 CONNECT 的 Central Board 進入 REPORTING 狀態。"""
        for gw_id, client in self._clients.items():
            try:
                client.wait_for_reporting(timeout=timeout)
                logger.info(f"[{gw_id}] 已進入 REPORTING")
            except GatewayTimeoutError:
                self._alert_bus.emit(GatewayAlert(
                    gw_id, AlertType.DATA_TIMEOUT, Severity.WARNING,
                    f"{gw_id} 等待 REPORTING 超時（{timeout}s）"
                ))

    # ── Callbacks ────────────────────────────────────────────────
    def on_ftms_data(self, callback: Callable[[TaggedFTMSData], None]) -> None:
        """
        登記統一 FTMS 回調。
        callback 收到的 TaggedFTMSData 帶有 gateway_id 欄位，可區分來源。
        """
        self._ftms_cbs.append(callback)

    def on_alert(self, callback: Callable[[GatewayAlert], None]) -> None:
        """登記警告回調。"""
        self._alert_bus.subscribe(callback)

    # ── Query ────────────────────────────────────────────────────
    def get_status_all(self) -> dict:
        """查詢所有 Central Board 狀態，回傳 {gw_id: StatusInfo}。"""
        result = {}
        for gw_id, client in self._clients.items():
            try:
                result[gw_id] = client.get_status()
            except Exception as e:
                logger.warning(f"[{gw_id}] get_status 失敗: {e}")
        return result

    def set_report_interval_all(self, ms: int) -> None:
        """設定所有 Central Board 的 FTMS 回報週期。"""
        for gw_id, client in self._clients.items():
            try:
                client.set_report_interval(ms)
            except Exception as e:
                logger.warning(f"[{gw_id}] set_report_interval 失敗: {e}")

    # ── Internal: init one board ─────────────────────────────────
    def _init_one(self, cfg) -> tuple:
        try:
            client = GatewayClient(
                port=cfg.port,
                baudrate=cfg.baudrate,
                timeout=cfg.cmd_timeout,
                rtscts=cfg.rtscts,
            )
            client.connect(boot_timeout=cfg.boot_timeout)
            boot = client.last_boot_info

            # 登記 FTMS 回調，附加 gateway_id tag
            client.on_ftms_data(
                lambda d, gid=cfg.id: self._dispatch_ftms(gid, d)
            )
            # 登記錯誤回調
            client.on_error(
                lambda e, gid=cfg.id: self._alert_bus.emit(
                    GatewayAlert(gid, AlertType.GW_ERROR, Severity.WARNING, str(e))
                )
            )
            # 登記非預期 BOOT 回調（板子在運行中重啟）
            client.on_unexpected_boot(
                lambda b, gid=cfg.id: self._handle_unexpected_boot(gid, b)
            )

            self._clients[cfg.id] = client
            logger.info(
                f"[{cfg.id}] 初始化成功 | "
                f"BOOT has_list={boot.has_list} count={boot.count}"
            )
            return cfg.id, boot

        except Exception as e:
            self._alert_bus.emit(GatewayAlert(
                cfg.id, AlertType.GW_INIT_FAILED, Severity.CRITICAL, str(e)
            ))
            logger.error(f"[{cfg.id}] 初始化失敗: {e}")
            return cfg.id, BootInfo(has_list=False)

    # ── Internal: scan one board (with retry) ────────────────────
    def _scan_one(self, cfg) -> tuple:
        client = self._clients.get(cfg.id)
        if not client:
            return cfg.id, []

        for attempt in range(cfg.scan_max_retries):
            try:
                devices = client.scan(cfg.scan_duration)
                if devices:
                    logger.info(f"[{cfg.id}] 掃描完成，發現 {len(devices)} 台設備")
                    return cfg.id, devices
                logger.warning(
                    f"[{cfg.id}] 掃描空結果 ({attempt + 1}/{cfg.scan_max_retries})"
                )
            except Exception as e:
                logger.warning(
                    f"[{cfg.id}] 掃描例外: {e} ({attempt + 1}/{cfg.scan_max_retries})"
                )

        self._alert_bus.emit(GatewayAlert(
            cfg.id, AlertType.SCAN_EMPTY, Severity.WARNING,
            f"掃描 {cfg.scan_max_retries} 次仍無設備"
        ))
        return cfg.id, []

    # ── Internal: FTMS dispatch ──────────────────────────────────
    def _dispatch_ftms(self, gw_id: str, data: FTMSData) -> None:
        self._last_data[data.mac] = time.monotonic()
        tagged = TaggedFTMSData(
            gateway_id=gw_id,
            mac=data.mac,
            device_type=data.device_type,
            rssi=data.rssi,
            timestamp=data.timestamp,
            instantaneous_speed=data.instantaneous_speed,
            stroke_rate=data.stroke_rate,
            instantaneous_pace=data.instantaneous_pace,
            total_distance=data.total_distance,
            instantaneous_power=data.instantaneous_power,
            total_energy=data.total_energy,
        )
        for cb in self._ftms_cbs:
            try:
                cb(tagged)
            except Exception as e:
                logger.warning(f"FTMS callback 例外: {e}")

    # ── Internal: unexpected BOOT handler ───────────────────────
    def _handle_unexpected_boot(self, gw_id: str, boot: BootInfo) -> None:
        """某塊板子在運行中重啟，對該板重走初始化→掃描→連線流程。"""
        self._alert_bus.emit(GatewayAlert(
            gw_id, AlertType.BOOT_UNEXPECTED, Severity.CRITICAL,
            f"{gw_id} 運行中重啟（has_list={boot.has_list}），將重新初始化連線"
        ))
        self._boots[gw_id] = boot

        cfg = self._cfgs[gw_id]
        client = self._clients.get(gw_id)
        if not client:
            return

        # 重走掃描→連線（在背景 thread 執行，不阻塞另一塊板）
        def recover():
            logger.info(f"[{gw_id}] 開始重新連線流程...")
            gw_id_result, devices = self._scan_one(cfg)
            scan_results = {gw_id: devices}
            assignments = self._assigner.assign(scan_results)
            macs = assignments.get(gw_id, [])
            if macs:
                try:
                    client.connect_devices(macs, wait=False)
                    logger.info(f"[{gw_id}] 重新連線 → {macs}")
                except Exception as e:
                    logger.error(f"[{gw_id}] 重新連線失敗: {e}")
            else:
                self._alert_bus.emit(GatewayAlert(
                    gw_id, AlertType.DEVICE_NOT_FOUND, Severity.WARNING,
                    f"{gw_id} 重啟後重掃仍找不到目標設備"
                ))

        threading.Thread(target=recover, name=f"cb-recover-{gw_id}", daemon=True).start()

    # ── Health monitor ───────────────────────────────────────────
    def _start_health_monitor(self) -> None:
        interval = min(c.health_check_interval for c in self._cfgs.values())

        def loop():
            while self._running:
                time.sleep(interval)
                self._health_check()

        self._health_thread = threading.Thread(
            target=loop, name="gw-health", daemon=True
        )
        self._health_thread.start()

    def _health_check(self) -> None:
        # STATUS 心跳
        for gw_id, client in self._clients.items():
            try:
                client.get_status()
            except Exception:
                self._alert_bus.emit(GatewayAlert(
                    gw_id, AlertType.GW_OFFLINE, Severity.CRITICAL,
                    f"{gw_id} STATUS 心跳無回應"
                ))

        # 資料逾時偵測
        now = time.monotonic()
        for gw_id, cfg in self._cfgs.items():
            for mac in cfg.target_macs:
                last = self._last_data.get(mac, 0)
                if last > 0 and (now - last) > cfg.data_timeout:
                    self._alert_bus.emit(GatewayAlert(
                        gw_id, AlertType.DATA_TIMEOUT, Severity.WARNING,
                        f"{mac} 超過 {cfg.data_timeout}s 無 FTMS 資料"
                    ))
