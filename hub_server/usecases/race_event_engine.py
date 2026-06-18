from collections import defaultdict
from typing import Dict, List, Set, Any
from hub_server.domain.models import RaceState


class RaceEventEngine:
    def __init__(self):
        self._checkpoints_passed: Dict[str, Set[int]] = defaultdict(set)
        self._segment_start: Dict[str, Dict[str, float]] = {}
        self._segment_best: Dict[int, Dict[str, Any]] = {}
        self._catch_up_history: Dict[str, float] = {}
        self._last_catch_up_emit: Dict[str, float] = {}
        self._countdown_triggered_at: Set[int] = set()
        self._final_sprint_triggered: bool = False
        self._prev_remaining_sec: int = 9999

    def reset(self):
        self._checkpoints_passed.clear()
        self._segment_start.clear()
        self._segment_best.clear()
        self._catch_up_history.clear()
        self._last_catch_up_emit.clear()
        self._countdown_triggered_at.clear()
        self._final_sprint_triggered = False
        self._prev_remaining_sec = -1

    def evaluate(self, race_manager, progress: Dict[str, Any]) -> List[Dict]:
        events: List[Dict] = []
        state = race_manager.get_state()
        if state != RaceState.RUNNING:
            return events

        config = race_manager.get_config()
        if not config:
            return events

        self._check_checkpoints(progress, config, events)
        self._check_catch_up(progress, config, events)
        self._check_countdown_or_sprint(progress, config, events)

        return events

    def _check_checkpoints(self, progress: Dict, config, events: List[Dict]):
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
                if threshold not in self._segment_best:
                    self._segment_best[threshold] = {
                        "node_id": node_id,
                        "athlete_name": node_progress.get("athlete_name", ""),
                        "station_number": node_progress.get("station_number"),
                        "segment_duration_ms": segment_duration,
                        "segment_value": segment_value,
                    }
                    is_fastest = True
                else:
                    best = self._segment_best[threshold]
                    if segment_duration < best["segment_duration_ms"]:
                        self._segment_best[threshold] = {
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

    def _check_countdown_or_sprint(self, progress: Dict, config, events: List[Dict]):
        race_type = config.race_type

        if race_type in ("time", "calories", "max_power", "watts"):
            max_elapsed = max(
                (p.get("elapsed_time_ms", 0) for p in progress.values()),
                default=0,
            )
            total_duration_ms = config.duration_sec * 1000
            remaining_ms = total_duration_ms - max_elapsed
            remaining_sec = max(0, int(remaining_ms / 1000))

            countdown_thresholds = [10, 5, 3, 2, 1]
            for ct in countdown_thresholds:
                if (
                    ct not in self._countdown_triggered_at
                    and self._prev_remaining_sec > ct >= remaining_sec
                ):
                    self._countdown_triggered_at.add(ct)
                    events.append(
                        {
                            "event_type": "countdown",
                            "data": {"seconds_left": ct},
                        }
                    )
            self._prev_remaining_sec = remaining_sec

        elif race_type == "distance":
            if self._final_sprint_triggered:
                return

            leader_progress = max(
                (p.get("progress_percent", 0) for p in progress.values()),
                default=0,
            )
            if leader_progress >= 85.0:
                self._final_sprint_triggered = True
                events.append(
                    {
                        "event_type": "final_sprint",
                        "data": {"leader_progress_pct": round(leader_progress, 1)},
                    }
                )

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
