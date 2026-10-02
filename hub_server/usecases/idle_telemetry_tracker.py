"""Standalone store for "idle live telemetry" -- the venue-visitor feature
that shows current speed/power/cadence/heart-rate on the dashboard while no
race is running.

This is deliberately a completely separate structure from RaceManager's
race progress (`_progress`), race results, records, or standings. It is
in-memory only, never persisted, and it must never influence scoring. See
CLAUDE.md's "Totally separate store" requirement.

Pure usecase code: no FastAPI, no MQTT, no asyncio -- just plain dicts and
an injectable clock (matching the pattern already used by NodeRegistry).
"""

import time
from typing import Any, Callable, Dict, Optional

# Metrics tracked for the idle "best of session" mini leaderboard. Kept to
# cheap, already-normalized telemetry fields -- see TELEMETRY_SPEC.md.
# heart_rate_bpm is deliberately NOT tracked here (product decision): the
# mini leaderboard drops it entirely to keep the row fitting on one line at
# 1280px wide. It still shows on every per-station card as usual -- this
# only affects the "best of session" aggregate.
BEST_METRICS = (
    "instantaneous_speed_kph",
    "power_watts",
    "cadence_rpm",
)

# Equipment types the dashboard shows a running pace for instead of raw
# speed (mirrors the dashboard's own isRunningEquipment in index.html).
TREADMILL_EQUIPMENT_TYPES = frozenset({"treadmill", "curved_treadmill"})

# The pseudo-metric key the mini leaderboard's treadmill-only "fastest pace"
# entry is stored under -- its value is the fastest treadmill SPEED seen
# (higher speed = faster pace); the dashboard formats it into a pace string
# with a single JS formatter (formatTreadmillPace), so no seconds-per-km
# conversion happens on this side.
TREADMILL_PACE_BEST_KEY = "treadmill_pace_speed_kph"


class IdleTelemetryTracker:
    def __init__(self, now_ms: Optional[Callable[[], int]] = None):
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._samples: Dict[str, Dict[str, Any]] = {}
        self._best: Dict[str, Dict[str, Any]] = {}

    def record_sample(
        self,
        node_id: str,
        metrics: Dict[str, Any],
        received_epoch_ms: Optional[int] = None,
    ) -> None:
        if received_epoch_ms is None:
            received_epoch_ms = self._now_ms()
        sample = dict(metrics)
        sample["received_epoch_ms"] = received_epoch_ms
        # When the device last reported speed > 0: a machine that keeps
        # sending zero-speed telemetry is idle, not live. Carried over from
        # the previous sample while stationary; None if it never moved.
        speed = metrics.get("instantaneous_speed_kph")
        if speed is not None and speed > 0:
            sample["last_moving_epoch_ms"] = received_epoch_ms
        else:
            previous = self._samples.get(node_id) or {}
            sample["last_moving_epoch_ms"] = previous.get("last_moving_epoch_ms")
        self._samples[node_id] = sample
        self._update_best(node_id, metrics)

    def _update_best(self, node_id: str, metrics: Dict[str, Any]) -> None:
        is_treadmill = metrics.get("equipment_type") in TREADMILL_EQUIPMENT_TYPES
        for metric_name in BEST_METRICS:
            if is_treadmill and metric_name in (
                "instantaneous_speed_kph",
                "power_watts",
            ):
                # A treadmill's speed isn't comparable to other equipment's
                # speed (it feeds the separate fastest-pace tracking below
                # instead), and treadmill power isn't shown in the idle view
                # at all -- see requirement B / renderIdleStationCard.
                continue
            value = metrics.get(metric_name)
            if value is None or value <= 0:
                continue
            current = self._best.get(metric_name)
            if current is None or value > current["value"]:
                self._best[metric_name] = {"value": value, "node_id": node_id}

        if is_treadmill:
            speed = metrics.get("instantaneous_speed_kph")
            if speed is not None and speed > 0:
                current = self._best.get(TREADMILL_PACE_BEST_KEY)
                if current is None or speed > current["value"]:
                    self._best[TREADMILL_PACE_BEST_KEY] = {
                        "value": speed,
                        "node_id": node_id,
                    }

    def get_sample(self, node_id: str) -> Optional[Dict[str, Any]]:
        sample = self._samples.get(node_id)
        return dict(sample) if sample is not None else None

    def get_best(self) -> Dict[str, Dict[str, Any]]:
        return {metric: dict(entry) for metric, entry in self._best.items()}

    def reset(self) -> None:
        self._samples.clear()
        self._best.clear()
