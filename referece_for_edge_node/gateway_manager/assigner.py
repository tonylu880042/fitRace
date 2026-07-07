"""
assigner.py — 設備分配器

根據掃描結果與設定策略，決定哪台設備由哪塊 Central Board 連線。

策略：
  STATIC  — 只連 config.target_macs 指定的設備
  RSSI    — Balanced Greedy：依訊號強弱 + 負載均衡自動分配
  HYBRID  — 優先 target_macs；找不到的 slot 改用 RSSI Balanced Greedy 補位
"""
import logging
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class _CandidateDevice:
    mac: str
    gw1_rssi: Optional[int]   # None 表示 GW1 未掃到此設備
    gw2_rssi: Optional[int]   # None 表示 GW2 未掃到此設備
    best_rssi: int             # max(gw1, gw2)，用於排序


class DeviceAssigner:
    def __init__(self, gw_cfgs: list, strategy: str):
        self._cfgs     = {c.id: c for c in gw_cfgs}
        self._strategy = strategy
        self._tie_db   = config.RSSI_TIE_THRESHOLD_DB
        self._max_per  = config.AUTO_MAX_PER_GW

    def assign(self, scan_results: dict) -> dict:
        """
        輸入：{gw_id: [DeviceInfo, ...]}
        輸出：{gw_id: [mac_str, ...]}，每塊板應連線的 MAC 清單。
        """
        if self._strategy == "static":
            return self._assign_static(scan_results)
        elif self._strategy == "rssi":
            return self._assign_balanced_greedy(scan_results, static_first={})
        else:  # hybrid
            static_hits = self._assign_static(scan_results)
            return self._assign_balanced_greedy(scan_results, static_first=static_hits)

    # ── STATIC ───────────────────────────────────────────────────
    def _assign_static(self, scan_results: dict) -> dict:
        result = {gw_id: [] for gw_id in self._cfgs}
        for gw_id, cfg in self._cfgs.items():
            found_macs = {d.mac for d in scan_results.get(gw_id, [])}
            for mac in cfg.target_macs:
                if mac in found_macs:
                    result[gw_id].append(mac)
                else:
                    logger.warning(f"[{gw_id}] 目標 MAC {mac} 未在掃描結果中找到")
        return result

    # ── BALANCED GREEDY ──────────────────────────────────────────
    def _assign_balanced_greedy(self, scan_results: dict, static_first: dict) -> dict:
        # 步驟 1：合併兩塊板的掃描結果，建立 mac → {gw_id: rssi} 對應表
        rssi_map: dict = {}
        for gw_id, devices in scan_results.items():
            for d in devices:
                rssi_map.setdefault(d.mac, {})[gw_id] = d.rssi

        # 步驟 2：建立候選設備清單，依 best_rssi 降冪排序（整體訊號最強者優先）
        candidates = [
            _CandidateDevice(
                mac=mac,
                gw1_rssi=rmap.get("GW1"),
                gw2_rssi=rmap.get("GW2"),
                best_rssi=max(rmap.values()),
            )
            for mac, rmap in rssi_map.items()
        ]
        candidates.sort(key=lambda c: c.best_rssi, reverse=True)

        # 步驟 3：靜態分配先佔位（HYBRID 用）
        assigned: dict = {gw_id: list(macs) for gw_id, macs in static_first.items()}
        assigned.setdefault("GW1", [])
        assigned.setdefault("GW2", [])
        already = {mac for macs in assigned.values() for mac in macs}

        # 步驟 4：逐一分配剩餘候選設備
        for dev in candidates:
            if dev.mac in already:
                continue

            gw1_full = len(assigned["GW1"]) >= self._max_per
            gw2_full = len(assigned["GW2"]) >= self._max_per
            if gw1_full and gw2_full:
                break

            # 只有一塊板掃到 → 直接分配給那塊板
            if dev.gw1_rssi is None:
                if not gw2_full:
                    assigned["GW2"].append(dev.mac)
                    already.add(dev.mac)
                continue
            if dev.gw2_rssi is None:
                if not gw1_full:
                    assigned["GW1"].append(dev.mac)
                    already.add(dev.mac)
                continue

            # 兩塊板都掃到 → 記錄重複，用決策核心選擇
            winner = self._pick_gw(
                r1=dev.gw1_rssi,
                r2=dev.gw2_rssi,
                cnt1=len(assigned["GW1"]),
                cnt2=len(assigned["GW2"]),
                full1=gw1_full,
                full2=gw2_full,
            )
            assigned[winner].append(dev.mac)
            already.add(dev.mac)

            logger.info(
                f"DUPLICATE {dev.mac}: GW1={dev.gw1_rssi}dBm GW2={dev.gw2_rssi}dBm "
                f"差={abs(dev.gw1_rssi - dev.gw2_rssi)}dBm "
                f"閾值={self._tie_db}dBm → {winner}"
            )

        return assigned

    # ── 決策核心：容差 + 負載均衡 ────────────────────────────────
    def _pick_gw(self, r1: int, r2: int,
                 cnt1: int, cnt2: int,
                 full1: bool, full2: bool) -> str:
        """
        決定分配給 GW1 或 GW2。優先順序：
          1. 某邊已滿 → 強制給另一邊
          2. RSSI 差值 ≥ 閾值 → 訊號明顯較強的贏
          3. RSSI 差值 < 閾值（容差內，視為相同）→ 已分配數量較少的贏
          4. 數量也相同 → GW1（確定性 fallback，保證結果可重現）
        """
        if full1:
            return "GW2"
        if full2:
            return "GW1"
        if abs(r1 - r2) >= self._tie_db:      # 訊號差異明顯
            return "GW1" if r1 > r2 else "GW2"
        if cnt1 != cnt2:                       # 容差內 → 負載均衡
            return "GW1" if cnt1 < cnt2 else "GW2"
        return "GW1"                           # 完全相同 → GW1 fallback
