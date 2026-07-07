"""
test_interactive.py — PC 互動式測試工具（單一 Central Board）

用於在開發機上快速驗證韌體通訊與指令行為，不需要跑完整的 GatewayManager。

用法：
  python test_interactive.py            # 使用 config.py 的 GW1 port
  python test_interactive.py COM4       # 指定 port
  python test_interactive.py COM4 True  # 第二參數 True = 開啟 RTS/CTS

操作選單：
  s  — 執行掃描（10 秒）
  c  — 選取設備並連線
  d  — DISCONNECT:ALL
  t  — 查詢 STATUS
  v  — 查詢 VERSION
  i  — 設定 REPORT 週期（ms）
  q  — 離開
"""
import logging
import sys
import time

import config
from logging_setup import setup_logging
from gateway_client import GatewayClient
from gateway_client.models import FTMSData, DeviceInfo

setup_logging()
logger = logging.getLogger(__name__)

# 每秒最多印一筆（避免畫面被資料淹沒）
_last_print: dict = {}


def _ftms_handler(data: FTMSData) -> None:
    now = time.monotonic()
    if now - _last_print.get(data.mac, 0) < 1.0:
        return
    _last_print[data.mac] = now
    print(
        f"  FTMS [{data.mac}] {data.device_type} | "
        f"speed={data.instantaneous_speed} km/h  "
        f"power={data.instantaneous_power} W  "
        f"rate={data.stroke_rate}  "
        f"dist={data.total_distance} m"
    )


def _do_scan(client: GatewayClient, duration: float = 10.0) -> list:
    print(f"\n掃描中...（{duration} 秒）")
    devices = client.scan(duration)
    if not devices:
        print("  (未發現任何設備)")
        return []
    print(f"  發現 {len(devices)} 台設備：")
    for i, d in enumerate(devices):
        print(f"    [{i}] {d.mac}  {d.rssi} dBm  {d.name}  {d.device_type}")
    return devices


def _do_connect(client: GatewayClient, devices: list) -> None:
    if not devices:
        print("  請先掃描（選單 s）")
        return
    raw = input("  輸入要連線的設備編號（空格分隔，最多 3 個）: ").strip()
    try:
        indices = [int(x) for x in raw.split()]
        macs = [devices[i].mac for i in indices if 0 <= i < len(devices)]
    except (ValueError, IndexError):
        print("  輸入無效")
        return
    if not macs:
        print("  沒有選擇任何設備")
        return
    print(f"  送出 CONNECT → {macs}")
    client.connect_devices(macs, wait=False)
    print("  等待 REPORTING（最多 30 秒）...")
    try:
        client.wait_for_reporting(timeout=30.0)
        print("  ✓ 已進入 REPORTING，開始接收 FTMS 資料")
    except Exception as e:
        print(f"  ✗ {e}")


def _menu(client: GatewayClient) -> None:
    client.on_ftms_data(_ftms_handler)
    client.on_error(lambda e: print(f"\n  ERROR: {e}"))
    client.on_unexpected_boot(
        lambda b: print(f"\n  !! 非預期 BOOT: has_list={b.has_list}")
    )

    devices: list = []
    print("\n指令: s=掃描  c=連線  d=DISCONNECT:ALL  t=STATUS  v=VERSION  i=REPORT週期  q=離開")

    while True:
        try:
            cmd = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if cmd == "q":
            break
        elif cmd == "s":
            devices = _do_scan(client)
        elif cmd == "c":
            _do_connect(client, devices)
        elif cmd == "d":
            print("  送出 DISCONNECT:ALL...")
            client.disconnect_all()
            print("  ✓ 已中斷所有連線，NVS MAC 清單已清空")
        elif cmd == "t":
            try:
                s = client.get_status()
                print(f"  STATUS: state={s.state}  conn={s.conn_count}  target={s.target_count}")
            except Exception as e:
                print(f"  ✗ {e}")
        elif cmd == "v":
            try:
                v = client.get_version()
                print(f"  VERSION: {v}")
            except Exception as e:
                print(f"  ✗ {e}")
        elif cmd == "i":
            raw = input("  輸入 REPORT 週期 ms（100–10000）: ").strip()
            try:
                ms = int(raw)
                client.set_report_interval(ms)
                print(f"  ✓ 已設定 REPORT:{ms}")
            except ValueError:
                print("  輸入無效")
            except Exception as e:
                print(f"  ✗ {e}")
        else:
            print("  未知指令")


def main() -> None:
    # 解析命令列參數
    port = sys.argv[1] if len(sys.argv) > 1 else config.GW1.port
    rtscts_arg = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    rtscts = rtscts_arg in ("1", "true", "yes")

    print(f"連線至 {port}  RTS/CTS={'開' if rtscts else '關'}")
    print("等待 Central Board BOOT 訊息...")

    try:
        with GatewayClient(port=port, baudrate=115200, rtscts=rtscts) as client:
            client.connect(boot_timeout=15.0)
            boot = client.last_boot_info
            print(
                f"✓ BOOT 成功: has_list={boot.has_list}, count={boot.count}\n"
            )
            _menu(client)
    except Exception as e:
        print(f"✗ 錯誤: {e}")
        sys.exit(1)

    print("已離開，再見。")


if __name__ == "__main__":
    main()
