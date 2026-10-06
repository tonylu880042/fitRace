import math
import time
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Set, Any
from hub_server.domain.models import RaceState

_CLOCK_COUNTDOWN_TYPES = ("time", "max_power", "watts")


class RaceEventEngine:
    def __init__(self, now_ms: Optional[Callable[[], int]] = None):
        # Hub clock for the timed-race countdown cues (injectable for tests).
        self._now_ms: Callable[[], int] = now_ms or (lambda: int(time.time() * 1000))
        self._start_ms: Optional[int] = None
        self._checkpoints_passed: Dict[str, Set[int]] = defaultdict(set)
        self._segment_start: Dict[str, Dict[str, float]] = {}
        self._segment_best: Dict[Any, Dict[str, Any]] = {}
        self._catch_up_history: Dict[str, float] = {}
        self._last_catch_up_emit: Dict[str, float] = {}
        self._countdown_triggered_at: Set[int] = set()
        self._final_sprint_triggered: bool = False
        self._prev_remaining_sec: int = 9999
        # Per-group dedupe state for a mixed race -- kept separate from the
        # (untouched) race-wide attributes above so a non-mixed race's
        # behaviour is byte-for-byte unchanged: it never reads or writes
        # these. Without this, group 2's countdown/final_sprint would be
        # silently suppressed by group 1's already having fired (or vice
        # versa), since the race-wide flags above are shared by the whole
        # evaluate() call regardless of which group produced progress.
        self._group_countdown_triggered_at: Dict[int, Set[int]] = defaultdict(set)
        self._group_final_sprint_triggered: Dict[int, bool] = {}
        self._group_prev_remaining_sec: Dict[int, int] = {}

    def reset(self):
        self._checkpoints_passed.clear()
        self._segment_start.clear()
        self._segment_best.clear()
        self._catch_up_history.clear()
        self._last_catch_up_emit.clear()
        self._countdown_triggered_at.clear()
        self._final_sprint_triggered = False
        self._prev_remaining_sec = -1
        self._group_countdown_triggered_at.clear()
        self._group_final_sprint_triggered.clear()
        self._group_prev_remaining_sec.clear()

    def evaluate_clock(self, race_manager, now_ms: int) -> List[Dict]:
        """Countdown cues only, from the hub clock -- for the periodic tick,
        so a silent treadmill (or samples ignored after the hub deadline)
        cannot swallow the last seconds. Shares dedupe state with
        evaluate(), so a cue fires once whichever path sees it first."""
        events: List[Dict] = []
        if (
            race_manager.get_state() != RaceState.RUNNING
            or race_manager.get_session_mode() != "race"
        ):
            return events
        config = race_manager.get_config()
        start_ms = race_manager.get_start_time_epoch_ms()
        if not config or start_ms is None:
            return events

        if config.race_type == "mixed":
            progress = race_manager.get_leaderboard_progress()
            for group_index in range(len(config.groups)):
                scoped = config.scoped_config(group_index)
                if scoped.race_type not in _CLOCK_COUNTDOWN_TYPES:
                    continue
                if not any(
                    r.get("group_index") == group_index for r in progress.values()
                ):
                    continue
                self._check_countdown_or_sprint(
                    {}, scoped, events, group_index, start_ms, now_ms
                )
        elif config.race_type in _CLOCK_COUNTDOWN_TYPES:
            self._check_countdown_or_sprint({}, config, events, None, start_ms, now_ms)
        return events

    def evaluate(self, race_manager, progress: Dict[str, Any]) -> List[Dict]:
        events: List[Dict] = []
        state = race_manager.get_state()
        if state != RaceState.RUNNING:
            return events

        config = race_manager.get_config()
        if not config:
            return events
        self._start_ms = race_manager.get_start_time_epoch_ms()

        if config.race_type == "mixed":
            # Run every _check_* helper once PER GROUP, against only that
            # group's own progress rows and its own scoped_config -- a row
            # with no group (group_index None, e.g. unmatched equipment) is
            # never passed to any group and so never generates an event.
            for group_index in range(len(config.groups)):
                group_progress = {
                    node_id: row
                    for node_id, row in progress.items()
                    if row.get("group_index") == group_index
                }
                if not group_progress:
                    continue
                scoped_config = config.scoped_config(group_index)
                self._check_checkpoints(
                    group_progress, scoped_config, events, group_index=group_index
                )
                self._check_catch_up(group_progress, scoped_config, events)
                self._check_countdown_or_sprint(
                    group_progress, scoped_config, events, group_index=group_index
                )
            return events

        self._check_checkpoints(progress, config, events)
        self._check_catch_up(progress, config, events)
        self._check_countdown_or_sprint(progress, config, events)

        return events

    def _segment_best_key(self, group_index: Optional[int], threshold: int):
        return threshold if group_index is None else (group_index, threshold)

    def _check_checkpoints(
        self,
        progress: Dict,
        config,
        events: List[Dict],
        group_index: Optional[int] = None,
    ):
        thresholds = [25, 50, 75]

        for node_id, node_progress in progress.items():
            if node_id.startswith("station-"):
                continue

            progress_pct = node_progress.get("progress_percent", 0)
            if node_progress.get("finished_time_ms") is not None:
                progress_pct = 100.0

            passed = self._checkpoints_passed.setdefault(node_id, set())

            if node_id not in self._segment_start:
                self._segment_start[node_id] = {
                    "elapsed_time_ms": 0,
                    "distance_m": 0,
                    "calories": 0,
                }

            for threshold in thresholds:
                if threshold in passed:
                    continue
                if progress_pct < threshold:
                    continue

                passed.add(threshold)
                start = self._segment_start.get(node_id, {})

                segment_duration = node_progress.get("elapsed_time_ms", 0) - start.get(
                    "elapsed_time_ms", 0
                )
                race_type = config.race_type
                segment_value = 0
                segment_unit = ""

                if race_type == "distance":
                    segment_value = node_progress.get("distance_m", 0) - start.get(
                        "distance_m", 0
                    )
                    segment_unit = "m"
                elif race_type == "calories":
                    segment_value = node_progress.get("calories", 0) - start.get(
                        "calories", 0
                    )
                    segment_unit = "kcal"
                elif race_type in ("time", "max_power", "watts"):
                    segment_value = node_progress.get("distance_m", 0) - start.get(
                        "distance_m", 0
                    )
                    segment_unit = "m"

                self._segment_start[node_id] = {
                    "elapsed_time_ms": node_progress.get("elapsed_time_ms", 0),
                    "distance_m": node_progress.get("distance_m", 0),
                    "calories": node_progress.get("calories", 0),
                }

                is_fastest = False
                best_key = self._segment_best_key(group_index, threshold)
                if best_key not in self._segment_best:
                    self._segment_best[best_key] = {
                        "node_id": node_id,
                        "athlete_name": node_progress.get("athlete_name", ""),
                        "station_number": node_progress.get("station_number"),
                        "segment_duration_ms": segment_duration,
                        "segment_value": segment_value,
                    }
                    is_fastest = True
                else:
                    best = self._segment_best[best_key]
                    if segment_duration < best["segment_duration_ms"]:
                        self._segment_best[best_key] = {
                            "node_id": node_id,
                            "athlete_name": node_progress.get("athlete_name", ""),
                            "station_number": node_progress.get("station_number"),
                            "segment_duration_ms": segment_duration,
                            "segment_value": segment_value,
                        }
                        is_fastest = True

                events.append(
                    {
                        "event_type": "checkpoint_crossed",
                        "data": {
                            "checkpoint_pct": threshold,
                            "node_id": node_id,
                            "athlete_name": node_progress.get("athlete_name", ""),
                            "station_number": node_progress.get("station_number"),
                            "segment_duration_ms": segment_duration,
                            "segment_value": round(segment_value, 1),
                            "segment_unit": segment_unit,
                            "is_fastest": is_fastest,
                        },
                    }
                )

    def _check_catch_up(self, progress: Dict, config, events: List[Dict]):
        race_type = config.race_type
        if race_type in ("max_power", "watts"):
            return

        nodes = [
            n
            for n in progress.values()
            if not n.get("node_id", "").startswith("station-")
        ]
        if len(nodes) < 2:
            return

        sorted_nodes = self._sort_nodes(nodes, race_type)

        import time

        now_ms = time.time() * 1000

        for i in range(len(sorted_nodes) - 1):
            target = sorted_nodes[i]
            chaser = sorted_nodes[i + 1]

            gap = 0
            gap_unit = ""
            is_small_gap = False

            if race_type == "distance":
                gap = target.get("distance_m", 0) - chaser.get("distance_m", 0)
                gap_unit = "m"
                is_small_gap = gap < 100
            elif race_type == "calories":
                gap = target.get("calories", 0) - chaser.get("calories", 0)
                gap_unit = "kcal"
                is_small_gap = gap < 20
            elif race_type == "time":
                gap = target.get("distance_m", 0) - chaser.get("distance_m", 0)
                gap_unit = "m"
                is_small_gap = gap < 100

            gap_key = f"{target['node_id']}->{chaser['node_id']}"
            prev_gap = self._catch_up_history.get(gap_key, gap)

            gap_ratio = gap / prev_gap if prev_gap > 0 else 1.0

            last_emit = self._last_catch_up_emit.get(gap_key, 0)

            chaser_is_eligible = (
                chaser.get("finished_time_ms") is None
                and chaser.get("progress_percent", 0) < 100
            )
            target_is_eligible = (
                target.get("finished_time_ms") is None
                and target.get("progress_percent", 0) < 100
            )

            if (
                gap > 0
                and is_small_gap
                and gap_ratio <= 0.92
                and (now_ms - last_emit) > 8000
                and chaser_is_eligible
                and target_is_eligible
            ):
                self._last_catch_up_emit[gap_key] = now_ms
                events.append(
                    {
                        "event_type": "catch_up_warning",
                        "data": {
                            "chaser_node_id": chaser["node_id"],
                            "chaser_name": chaser.get("athlete_name", ""),
                            "chaser_station": chaser.get("station_number"),
                            "target_node_id": target["node_id"],
                            "target_name": target.get("athlete_name", ""),
                            "target_station": target.get("station_number"),
                            "gap": round(gap, 1),
                            "gap_unit": gap_unit,
                            "rank": i + 1,
                        },
                    }
                )

            self._catch_up_history[gap_key] = gap

    def _check_countdown_or_sprint(
        self,
        progress: Dict,
        config,
        events: List[Dict],
        group_index: Optional[int] = None,
        start_ms: Optional[int] = None,
        now_ms: Optional[int] = None,
    ):
        race_type = config.race_type

        if race_type in ("time", "calories", "max_power", "watts"):
            total_duration_ms = config.duration_sec * 1000
            if start_ms is None:
                start_ms = self._start_ms
            if race_type in _CLOCK_COUNTDOWN_TYPES and start_ms is not None:
                # Hub clock: equipment elapsed can stall or be ignored.
                if now_ms is None:
                    now_ms = self._now_ms()
                remaining_ms = start_ms + total_duration_ms - now_ms
                # Round UP: threshold N fires when N s (not N+1 s) remain.
                remaining_sec = max(0, math.ceil(remaining_ms / 1000))
            else:
                max_elapsed = max(
                    (p.get("elapsed_time_ms", 0) for p in progress.values()),
                    default=0,
                )
                remaining_ms = total_duration_ms - max_elapsed
                remaining_sec = max(0, int(remaining_ms / 1000))

            countdown_thresholds = [10, 5, 3, 2, 1]
            triggered_at = self._countdown_state(group_index)
            prev_remaining_sec = self._get_prev_remaining_sec(group_index)
            for ct in countdown_thresholds:
                if ct not in triggered_at and prev_remaining_sec > ct >= remaining_sec:
                    triggered_at.add(ct)
                    events.append(
                        {
                            "event_type": "countdown",
                            "data": {"seconds_left": ct},
                        }
                    )
            self._set_prev_remaining_sec(group_index, remaining_sec)

        elif race_type == "distance":
            if self._get_final_sprint_triggered(group_index):
                return

            leader_progress = max(
                (p.get("progress_percent", 0) for p in progress.values()),
                default=0,
            )
            if leader_progress >= 85.0:
                self._set_final_sprint_triggered(group_index, True)
                events.append(
                    {
                        "event_type": "final_sprint",
                        "data": {"leader_progress_pct": round(leader_progress, 1)},
                    }
                )

    def _countdown_state(self, group_index: Optional[int]) -> Set[int]:
        """The race-wide set for a non-mixed race (unchanged, so a
        non-mixed race's dedupe behaviour stays byte-for-byte identical),
        or this group's own set in a mixed race."""
        if group_index is None:
            return self._countdown_triggered_at
        return self._group_countdown_triggered_at[group_index]

    def _get_final_sprint_triggered(self, group_index: Optional[int]) -> bool:
        if group_index is None:
            return self._final_sprint_triggered
        return self._group_final_sprint_triggered.get(group_index, False)

    def _set_final_sprint_triggered(self, group_index: Optional[int], value: bool):
        if group_index is None:
            self._final_sprint_triggered = value
        else:
            self._group_final_sprint_triggered[group_index] = value

    def _get_prev_remaining_sec(self, group_index: Optional[int]) -> int:
        if group_index is None:
            return self._prev_remaining_sec
        return self._group_prev_remaining_sec.get(group_index, 9999)

    def _set_prev_remaining_sec(self, group_index: Optional[int], value: int):
        if group_index is None:
            self._prev_remaining_sec = value
        else:
            self._group_prev_remaining_sec[group_index] = value

    def _sort_nodes(self, nodes: List[Dict], race_type: str) -> List[Dict]:
        import copy

        sorted_nodes = copy.deepcopy(nodes)

        def station_sort_key(n):
            sn = n.get("station_number")
            if sn is None:
                return 999
            return int(sn)

        if race_type == "distance":
            sorted_nodes.sort(
                key=lambda n: (
                    -(
                        1
                        if (
                            n.get("finished_time_ms") is not None
                            or n.get("progress_percent", 0) >= 100
                        )
                        else 0
                    ),
                    n.get("finished_time_ms") or float("inf"),
                    -n.get("progress_percent", 0),
                    -n.get("distance_m", 0),
                    -n.get("instantaneous_speed_kph", 0),
                    station_sort_key(n),
                )
            )
        elif race_type == "calories":
            sorted_nodes.sort(
                key=lambda n: (
                    -(
                        1
                        if (
                            n.get("finished_time_ms") is not None
                            or n.get("progress_percent", 0) >= 100
                        )
                        else 0
                    ),
                    n.get("finished_time_ms") or float("inf"),
                    -n.get("calories", 0),
                    -n.get("power_watts", 0),
                    station_sort_key(n),
                )
            )
        elif race_type == "time":
            sorted_nodes.sort(
                key=lambda n: (
                    -n.get("distance_m", 0),
                    -n.get("instantaneous_speed_kph", 0),
                    station_sort_key(n),
                )
            )
        elif race_type in ("max_power", "watts"):
            sorted_nodes.sort(
                key=lambda n: (
                    -n.get("max_power_watts", 0),
                    -n.get("power_watts", 0),
                    station_sort_key(n),
                )
            )

        return sorted_nodes
