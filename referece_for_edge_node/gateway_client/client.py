"""
client.py — BLE Central Board 單一 UART 客戶端

背景 reader thread 持續讀取 UART，解析後：
  - 觸發已登記的 callback（FTMS、DEVICE、ERROR）
  - 喚醒 blocking 等待（BOOT、OK、STATUS、VERSION）

執行緒模型：
  reader thread  → 讀 UART bytes → 拼行 → dispatch_queue
  dispatcher thread → 從 queue 取行 → parse_line() → 分派

使用方式：
  with GatewayClient(port="COM3").connect() as client:
      boot = client.last_boot_info
      devices = client.scan(10)
      client.connect_devices([d.mac for d in devices[:2]])
      ...
"""
import logging
import queue
import threading
import time
from typing import Callable, Optional

import serial

from .exceptions import GatewayConnectionError, GatewayTimeoutError, GatewayCommandError
from .models import BootInfo, DeviceInfo, FTMSData, StatusInfo
from .protocol import (
    parse_line,
    build_scan_start, build_scan_stop,
    build_connect, build_disconnect_all, build_report_interval,
    build_status, build_version,
)

logger = logging.getLogger(__name__)


class GatewayClient:
    def __init__(self, port: str, baudrate: int = 115200,
                 timeout: float = 10.0, rtscts: bool = False):
        self.port     = port
        self.baudrate = baudrate
        self.timeout  = timeout
        self.rtscts   = rtscts

        self._serial: Optional[serial.Serial] = None
        self._running = False

        # Threads
        self._reader_thread: Optional[threading.Thread] = None
        self._dispatcher_thread: Optional[threading.Thread] = None
        self._dispatch_queue: queue.Queue = queue.Queue()

        # Callbacks
        self._ftms_cbs:   list = []
        self._device_cbs: list = []
        self._error_cbs:  list = []
        self._unexp_boot_cbs: list = []   # 運行中收到非預期 BOOT

        # Blocking waiters
        self._boot_event   = threading.Event()
        self._status_event = threading.Event()
        self._version_event = threading.Event()
        self._reporting_event = threading.Event()

        self._status_result:  Optional[StatusInfo] = None
        self._version_result: Optional[str] = None

        # OK waiter：cmd_name → Event
        self._ok_events: dict = {}
        self._ok_lock = threading.Lock()

        # Scan state
        self._scan_active = False
        self._scan_results: dict = {}   # mac → DeviceInfo
        self._scan_lock = threading.Lock()

        # 初始化完成標記（用於偵測非預期 BOOT）
        self._initialized = False

        # Public
        self.last_boot_info: Optional[BootInfo] = None

    # ── Context manager ──────────────────────────────────────────
    def __enter__(self) -> "GatewayClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Connect / Close ──────────────────────────────────────────
    def connect(self, boot_timeout: Optional[float] = None) -> "GatewayClient":
        """
        開啟 serial port，啟動 reader thread，送出 PING 等待 BOOT 回應。

        新流程（配合韌體 PING 指令）：
          Pi 開機後主動送 PING; → 板子回應 BOOT:HAS_LIST,count=N; 或 BOOT:NO_LIST;
          避免 Pi 尚未就緒時板子的 BOOT 自動推送被遺漏。
        """
        bt = boot_timeout if boot_timeout is not None else self.timeout
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                rtscts=self.rtscts,
                timeout=0.1,
            )
        except serial.SerialException as e:
            raise GatewayConnectionError(f"無法開啟 {self.port}: {e}") from e

        self._running = True
        self._boot_event.clear()

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"cb-reader-{self.port}",
            daemon=True,
        )
        self._dispatcher_thread = threading.Thread(
            target=self._dispatcher_loop,
            name=f"cb-disp-{self.port}",
            daemon=True,
        )
        self._reader_thread.start()
        self._dispatcher_thread.start()

        # 送出 PING，讓板子回應目前的 BOOT 狀態
        # 每隔 _PING_INTERVAL 秒重送一次，直到收到 BOOT 回應或超時
        _PING_INTERVAL = 2.0
        _deadline = time.monotonic() + bt
        logger.info(f"[{self.port}] 送出 PING，等待 BOOT 回應（最多 {bt}s）...")
        while True:
            self._send("PING;\r\n")
            remaining = _deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._boot_event.wait(timeout=min(_PING_INTERVAL, remaining)):
                break   # 收到 BOOT 回應

        if not self._boot_event.is_set():
            self.close()
            raise GatewayTimeoutError(f"{self.port}: 等待 BOOT 回應超時（{bt}s）")

        self._initialized = True
        return self

    def close(self) -> None:
        """停止 reader/dispatcher threads，關閉 serial port。"""
        self._running = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._dispatch_queue.put(None)   # 解除 dispatcher 阻塞
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
        if self._dispatcher_thread:
            self._dispatcher_thread.join(timeout=2)
        logger.info(f"[{self.port}] 已關閉")

    # ── Callbacks ────────────────────────────────────────────────
    def on_ftms_data(self, callback: Callable[[FTMSData], None]) -> None:
        """登記 FTMS 資料回調，在 dispatcher thread 執行。"""
        self._ftms_cbs.append(callback)

    def on_device_found(self, callback: Callable[[DeviceInfo], None]) -> None:
        """登記掃描設備發現回調。"""
        self._device_cbs.append(callback)

    def on_error(self, callback: Callable[[str], None]) -> None:
        """登記 Central Board 錯誤訊息回調。"""
        self._error_cbs.append(callback)

    def on_unexpected_boot(self, callback: Callable[[BootInfo], None]) -> None:
        """登記非預期 BOOT 回調（Central Board 在運行中重啟）。"""
        self._unexp_boot_cbs.append(callback)

    # ── Scan ─────────────────────────────────────────────────────
    def scan(self, duration: float = 10.0) -> list:
        """
        掃描 duration 秒，回傳 [DeviceInfo]，依 RSSI 由強到弱排序。
        同一台設備出現多次時，保留 RSSI 最強的那筆。
        """
        with self._scan_lock:
            self._scan_results.clear()
            self._scan_active = True

        self._send(build_scan_start())
        time.sleep(duration)
        self._send(build_scan_stop())
        time.sleep(0.3)   # 讓最後幾筆 DEVICE 訊息有時間進來

        with self._scan_lock:
            self._scan_active = False
            devices = list(self._scan_results.values())

        return sorted(devices, key=lambda d: d.rssi, reverse=True)

    # ── BLE connect / disconnect ──────────────────────────────────
    def connect_devices(self, macs: list, wait: bool = True) -> None:
        """
        送出 CONNECT 指令（最多 3 台）。
        wait=True 時阻塞直到收到第一筆 FTMS 資料（REPORTING 狀態）。
        """
        if not macs:
            return
        self._reporting_event.clear()
        self._send(build_connect(*macs[:3]))
        self._wait_ok("CONNECT", timeout=self.timeout)
        if wait:
            self.wait_for_reporting(timeout=self.timeout * 6)

    def disconnect_all(self) -> None:
        """送出 DISCONNECT:ALL，等待 OK，清空 NVS MAC 清單。"""
        self._reporting_event.clear()
        self._send(build_disconnect_all())
        self._wait_ok("DISCONNECT", timeout=self.timeout)

    def set_report_interval(self, ms: int) -> None:
        """動態調整 FTMS 回報週期（100–10000 ms）。"""
        self._send(build_report_interval(ms))

    # ── Query ────────────────────────────────────────────────────
    def get_status(self) -> StatusInfo:
        """送出 STATUS，阻塞等待回應。"""
        self._status_event.clear()
        self._status_result = None
        self._send(build_status())
        if not self._status_event.wait(timeout=self.timeout):
            raise GatewayTimeoutError(f"{self.port}: STATUS 回應超時")
        return self._status_result

    def get_version(self) -> str:
        """送出 VERSION，阻塞等待回傳版本字串。"""
        self._version_event.clear()
        self._version_result = None
        self._send(build_version())
        if not self._version_event.wait(timeout=self.timeout):
            raise GatewayTimeoutError(f"{self.port}: VERSION 回應超時")
        return self._version_result

    def wait_for_reporting(self, timeout: float = 30.0) -> None:
        """阻塞直到收到第一筆 FTMS 資料（Central Board 進入 REPORTING）。"""
        if not self._reporting_event.wait(timeout=timeout):
            raise GatewayTimeoutError(
                f"{self.port}: 等待 REPORTING 超時（{timeout}s）"
            )

    # ── Internal: send ───────────────────────────────────────────
    def _send(self, cmd: str) -> None:
        if not (self._serial and self._serial.is_open):
            raise GatewayConnectionError(f"{self.port}: serial 未開啟")
        try:
            self._serial.write(cmd.encode("ascii"))
            logger.debug(f"[{self.port}] TX: {cmd.strip()}", extra={"tag": "UART"})
        except serial.SerialException as e:
            raise GatewayConnectionError(f"{self.port}: 寫入失敗 {e}") from e

    # ── Internal: reader thread ───────────────────────────────────
    def _reader_loop(self) -> None:
        buf = b""
        while self._running:
            try:
                chunk = self._serial.read(256)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line_b, buf = buf.split(b"\n", 1)
                    line = line_b.decode("ascii", errors="replace")
                    logger.debug(f"[{self.port}] RX: {line.strip()}", extra={"tag": "UART"})
                    self._dispatch_queue.put(line)
            except serial.SerialException:
                if self._running:
                    logger.error(f"[{self.port}] UART 讀取中斷")
                break
            except Exception as e:
                if self._running:
                    logger.warning(f"[{self.port}] reader 例外: {e}")

    # ── Internal: dispatcher thread ──────────────────────────────
    def _dispatcher_loop(self) -> None:
        while self._running:
            try:
                line = self._dispatch_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                self._handle_line(line)
            except Exception as e:
                logger.warning(f"[{self.port}] handle_line 例外: {e}")

    def _handle_line(self, line: str) -> None:
        result = parse_line(line)
        if result is None:
            return
        msg_type, obj = result

        if msg_type == "boot":
            if self._initialized:
                # 運行中收到 BOOT → Central Board 重啟
                logger.warning(f"[{self.port}] 收到非預期 BOOT: {obj}")
                self._initialized = False
                self.last_boot_info = obj
                for cb in self._unexp_boot_cbs:
                    try:
                        cb(obj)
                    except Exception:
                        pass
            else:
                self.last_boot_info = obj
                self._boot_event.set()
                logger.info(
                    f"[{self.port}] BOOT: has_list={obj.has_list}, count={obj.count}"
                )

        elif msg_type == "device":
            with self._scan_lock:
                if self._scan_active:
                    existing = self._scan_results.get(obj.mac)
                    if existing is None or obj.rssi > existing.rssi:
                        self._scan_results[obj.mac] = obj
            for cb in self._device_cbs:
                try:
                    cb(obj)
                except Exception:
                    pass

        elif msg_type == "ftms":
            self._reporting_event.set()
            for cb in self._ftms_cbs:
                try:
                    cb(obj)
                except Exception:
                    pass

        elif msg_type == "status":
            self._status_result = obj
            self._status_event.set()

        elif msg_type == "version":
            self._version_result = obj
            self._version_event.set()

        elif msg_type == "ok":
            cmd_name = obj
            with self._ok_lock:
                ev = self._ok_events.get(cmd_name)
            if ev:
                ev.set()

        elif msg_type == "error":
            logger.warning(f"[{self.port}] Central Board ERROR: {obj}")
            for cb in self._error_cbs:
                try:
                    cb(str(obj))
                except Exception:
                    pass

    def _wait_ok(self, cmd_name: str, timeout: float) -> None:
        ev = threading.Event()
        with self._ok_lock:
            self._ok_events[cmd_name] = ev
        try:
            if not ev.wait(timeout=timeout):
                raise GatewayTimeoutError(
                    f"{self.port}: {cmd_name}:OK 超時（{timeout}s）"
                )
        finally:
            with self._ok_lock:
                self._ok_events.pop(cmd_name, None)
