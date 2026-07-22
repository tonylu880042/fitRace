from typing import Dict, Any, Optional
from hub_server.domain.models import RaceState, RaceConfig


class RaceManager:
    VALID_LEADERBOARD_DISPLAY_MODES = {
        "classic",
        "race_track",
        "team_battle",
        "sprint_board",
    }

    def __init__(self, settings_store=None):
        self._state: RaceState = RaceState.IDLE
        self._config: Optional[RaceConfig] = None
        self._leaderboard_display_mode: str = "classic"
        self._start_countdown_sound_enabled: bool = True
        self._settings_store = settings_store
        self._registered_nodes: Dict[str, str] = {}  # node_id -> athlete_name (for legacy backward compatibility)
        self._progress: Dict[str, Dict[str, Any]] = {}  # node_id -> metrics dict
        
        # New station mapping structures
        self._stations: Dict[int, str] = {}  # station_number (int) -> node_id (str)
        self._station_registrations: Dict[int, str] = {}  # station_number (int) -> athlete_name (str)
        self._station_teams: Dict[int, Optional[str]] = {}  # station_number (int) -> team_name (str)
        self._station_has_avatar: Dict[int, bool] = {}  # station_number (int) -> has_avatar (bool)
        self._active_nodes: Dict[str, str] = {}  # node_id (str) -> equipment_type (str)
        self._start_time_epoch_ms: Optional[int] = None
        self._end_time_epoch_ms: Optional[int] = None

        self._load_settings()

    # -- durable settings persistence ---------------------------------
    # Only operator-configured settings survive a restart. Transient race
    # state (running/progress/registrations) always comes back IDLE/empty.

    def _load_settings(self) -> None:
        if not self._settings_store:
            return
        data = self._settings_store.load()
        if not data:
            return
        stations = data.get("stations")
        if isinstance(stations, dict):
            self._stations = {
                int(sn): nid for sn, nid in stations.items() if nid
            }
        mode = data.get("leaderboard_display_mode")
        if mode in self.VALID_LEADERBOARD_DISPLAY_MODES:
            self._leaderboard_display_mode = mode
        if isinstance(data.get("start_countdown_sound_enabled"), bool):
            self._start_countdown_sound_enabled = data["start_countdown_sound_enabled"]
        config = data.get("config")
        if isinstance(config, dict):
            try:
                self._config = RaceConfig.model_validate(config)
            except Exception:
                self._config = None

    def _persist_settings(self) -> None:
        if not self._settings_store:
            return
        self._settings_store.save(
            {
                "stations": {str(sn): nid for sn, nid in self._stations.items()},
                "leaderboard_display_mode": self._leaderboard_display_mode,
                "start_countdown_sound_enabled": self._start_countdown_sound_enabled,
                "config": self._config.model_dump() if self._config else None,
            }
        )

    def get_state(self) -> RaceState:
        return self._state

    def get_config(self) -> Optional[RaceConfig]:
        return self._config

    def get_start_time_epoch_ms(self) -> Optional[int]:
        return self._start_time_epoch_ms

    def get_end_time_epoch_ms(self) -> Optional[int]:
        return self._end_time_epoch_ms

    def get_leaderboard_display_mode(self) -> str:
        return self._leaderboard_display_mode

    def set_leaderboard_display_mode(self, mode: str) -> str:
        if mode not in self.VALID_LEADERBOARD_DISPLAY_MODES:
            raise ValueError(f"Unsupported leaderboard display mode: {mode}")
        self._leaderboard_display_mode = mode
        self._persist_settings()
        return self._leaderboard_display_mode

    def get_start_countdown_sound_enabled(self) -> bool:
        return self._start_countdown_sound_enabled

    def set_start_countdown_sound_enabled(self, enabled: bool) -> bool:
        self._start_countdown_sound_enabled = bool(enabled)
        self._persist_settings()
        return self._start_countdown_sound_enabled

    def get_state_snapshot(self) -> Dict[str, Any]:
        config = self.get_config()
        team_leaderboard = (
            self.get_team_leaderboard_progress()
            if config and config.competition_mode == "team"
            else []
        )
        return {
            "state": self.get_state().value,
            "config": config.model_dump() if config else None,
            "registered_nodes": self.get_registered_nodes(),
            "start_time_epoch_ms": self.get_start_time_epoch_ms(),
            "end_time_epoch_ms": self.get_end_time_epoch_ms(),
            "leaderboard_display_mode": self.get_leaderboard_display_mode(),
            "start_countdown_sound_enabled": self.get_start_countdown_sound_enabled(),
            "leaderboard": self.get_leaderboard_progress(),
            "team_leaderboard": team_leaderboard,
        }

    def get_leaderboard_progress(self) -> Dict[str, Dict[str, Any]]:
        if self._state in (RaceState.RUNNING, RaceState.STOPPED):
            return self._progress

        progress = {}
        import time
        
        # 1. Initialize from legacy registered_nodes
        for node_id, athlete_name in self._registered_nodes.items():
            station_number = None
            for sn, nid in self._stations.items():
                if nid == node_id:
                    station_number = sn
                    break
            progress[node_id] = {
                "node_id": node_id,
                "athlete_name": athlete_name,
                "station_number": station_number,
                "team_name": None,
                "avatar_url": None,
                "distance_m": 0.0,
                "elapsed_time_ms": 0,
                "instantaneous_speed_kph": 0.0,
                "progress_percent": 0.0,
                "calories": 0.0,
                "power_watts": 0,
                "max_power_watts": 0,
                "finished_time_ms": None,
            }
            
        # 2. Initialize from station registrations
        for station_number, athlete_name in self._station_registrations.items():
            node_id = self._stations.get(station_number)
            key = node_id if node_id else f"station-{station_number}"
            if key not in progress:
                team_name = self._station_teams.get(station_number)
                has_avatar = self._station_has_avatar.get(station_number, False)
                avatar_url = f"/static/avatars/station_{station_number}.webp?t={int(time.time())}" if has_avatar else None
                progress[key] = {
                    "node_id": node_id or key,
                    "athlete_name": athlete_name,
                    "station_number": station_number,
                    "team_name": team_name,
                    "avatar_url": avatar_url,
                    "distance_m": 0.0,
                    "elapsed_time_ms": 0,
                    "instantaneous_speed_kph": 0.0,
                    "progress_percent": 0.0,
                    "calories": 0.0,
                    "power_watts": 0,
                    "max_power_watts": 0,
                    "finished_time_ms": None,
                }
        return progress

    def get_team_leaderboard_progress(self) -> list[Dict[str, Any]]:
        config = self.get_config()
        if not config or config.competition_mode != "team":
            return []

        teams: Dict[str, Dict[str, Any]] = {}
        for node in self.get_leaderboard_progress().values():
            team_name = (node.get("team_name") or "").strip() or "Unassigned"
            team = teams.setdefault(
                team_name,
                {
                    "team_name": team_name,
                    "member_count": 0,
                    "finished_count": 0,
                    "distance_m": 0.0,
                    "calories": 0.0,
                    "power_watts": 0,
                    "max_power_watts": 0,
                    "elapsed_time_ms": 0,
                    "progress_total": 0.0,
                    "team_finished_time_ms": None,
                    "members": [],
                },
            )

            progress_percent = self._metric_number(node.get("progress_percent"))
            team_progress_percent = (
                min(100.0, progress_percent)
                if config.team_completion_policy == "all_members" and config.race_type in ("distance", "calories")
                else progress_percent
            )
            distance_m = self._metric_number(node.get("distance_m"))
            calories = self._metric_number(node.get("calories"))
            power_watts = int(self._metric_number(node.get("power_watts")))
            max_power_watts = int(self._metric_number(node.get("max_power_watts")))
            elapsed_time_ms = int(self._metric_number(node.get("elapsed_time_ms")))

            team["member_count"] += 1
            team["finished_count"] += 1 if node.get("finished_time_ms") is not None else 0
            team["distance_m"] += distance_m
            team["calories"] += calories
            team["power_watts"] += power_watts
            team["max_power_watts"] += max_power_watts
            team["elapsed_time_ms"] = max(team["elapsed_time_ms"], elapsed_time_ms)
            team["progress_total"] += team_progress_percent
            if node.get("finished_time_ms") is not None:
                finished_time_ms = int(self._metric_number(node.get("finished_time_ms")))
                team["team_finished_time_ms"] = max(
                    team["team_finished_time_ms"] or 0,
                    finished_time_ms,
                )
            team["members"].append(
                {
                    "node_id": node.get("node_id"),
                    "athlete_name": node.get("athlete_name"),
                    "station_number": node.get("station_number"),
                    "avatar_url": node.get("avatar_url"),
                    "distance_m": round(distance_m, 2),
                    "calories": round(calories, 2),
                    "power_watts": power_watts,
                    "max_power_watts": max_power_watts,
                    "elapsed_time_ms": elapsed_time_ms,
                    "progress_percent": round(team_progress_percent, 2),
                    "finished_time_ms": node.get("finished_time_ms"),
                }
            )

        leaderboard = []
        for team in teams.values():
            member_count = team["member_count"] or 1
            average_progress = team["progress_total"] / member_count
            score_value, progress_percent, score_label = self._team_score(team, config)
            team_finished = self._team_finished(team, config, progress_percent)
            team_finished_time_ms = team["team_finished_time_ms"] if team_finished else None

            team["members"].sort(
                key=lambda member: self._metric_number(member.get("station_number"), 999)
            )
            leaderboard.append(
                {
                    "team_name": team["team_name"],
                    "member_count": team["member_count"],
                    "finished_count": team["finished_count"],
                    "distance_m": round(team["distance_m"], 2),
                    "calories": round(team["calories"], 2),
                    "power_watts": team["power_watts"],
                    "max_power_watts": team["max_power_watts"],
                    "elapsed_time_ms": team["elapsed_time_ms"],
                    "progress_percent": round(progress_percent, 2),
                    "average_progress_percent": round(average_progress, 2),
                    "score_value": round(score_value, 2),
                    "score_label": score_label,
                    "scoring_policy": config.team_scoring_policy,
                    "completion_policy": config.team_completion_policy,
                    "team_finished": team_finished,
                    "team_finished_time_ms": team_finished_time_ms,
                    "members": team["members"],
                }
            )

        self._sort_team_leaderboard(leaderboard, config)
        return leaderboard

    def _team_score(self, team: Dict[str, Any], config: RaceConfig) -> tuple[float, float, str]:
        member_count = team["member_count"] or 1
        policy = config.team_scoring_policy

        if config.race_type == "distance":
            if config.team_completion_policy == "all_members":
                score = team["progress_total"] / member_count
                return score, score, "progress"
            if policy == "total":
                score = team["distance_m"]
                progress = (team["distance_m"] / config.target_value) * 100.0
            else:
                score = team["progress_total"] / member_count
                progress = score
            return score, progress, "distance_m"

        if config.race_type == "calories":
            if config.team_completion_policy == "all_members":
                score = team["progress_total"] / member_count
                return score, score, "progress"
            if policy == "total":
                score = team["calories"]
                progress = (team["calories"] / config.target_value) * 100.0
            else:
                score = team["progress_total"] / member_count
                progress = score
            return score, progress, "calories"

        if config.race_type == "time":
            if policy == "total":
                score = team["distance_m"]
            else:
                score = team["distance_m"] / member_count
            return score, team["progress_total"] / member_count, "distance_m"

        if config.race_type in ("max_power", "watts"):
            if policy == "total":
                score = team["max_power_watts"]
            else:
                score = team["max_power_watts"] / member_count
            return score, team["progress_total"] / member_count, "max_power_watts"

        return team["progress_total"] / member_count, team["progress_total"] / member_count, "progress"

    def _team_finished(self, team: Dict[str, Any], config: RaceConfig, progress_percent: float) -> bool:
        if config.team_completion_policy == "all_members" and config.race_type in ("distance", "calories"):
            return team["member_count"] > 0 and team["finished_count"] == team["member_count"]
        if config.race_type in ("distance", "calories"):
            return progress_percent >= 100.0
        return self._state == RaceState.STOPPED

    def _sort_team_leaderboard(self, leaderboard: list[Dict[str, Any]], config: RaceConfig):
        if config.team_completion_policy == "all_members" and config.race_type in ("distance", "calories"):
            leaderboard.sort(
                key=lambda team: (
                    0 if team.get("team_finished") else 1,
                    self._metric_number(team.get("team_finished_time_ms"), float("inf")),
                    -self._metric_number(team.get("average_progress_percent")),
                    str(team.get("team_name") or ""),
                )
            )
            return

        leaderboard.sort(
            key=lambda team: (
                -self._metric_number(team.get("score_value")),
                -self._metric_number(team.get("average_progress_percent")),
                -self._metric_number(team.get("finished_count")),
                str(team.get("team_name") or ""),
            )
        )

    @staticmethod
    def _metric_number(value: Any, fallback: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return fallback
        return number

    def get_registered_nodes(self) -> Dict[str, str]:
        # Merge stations and registered nodes to maintain backward compatibility
        merged = dict(self._registered_nodes)
        for sn, nid in self._stations.items():
            if nid:
                ath_name = self._station_registrations.get(sn)
                if ath_name:
                    merged[nid] = ath_name
        return merged

    def configure(self, config: RaceConfig):
        # We can configure in IDLE, READY, or STOPPED state
        if self._state not in (RaceState.IDLE, RaceState.READY, RaceState.STOPPED):
            raise ValueError(f"Cannot configure race in state {self._state}")
        
        if self._state == RaceState.STOPPED:
            self._start_time_epoch_ms = None
            self._end_time_epoch_ms = None
            self._registered_nodes.clear()
            self._progress.clear()
            # Clean current athlete registrations but keep hardware station mapping
            self._station_registrations.clear()
            self._station_teams.clear()
            self._station_has_avatar.clear()
            self._active_nodes.clear()

        self._config = config
        self._state = RaceState.READY
        self._persist_settings()

    def register_node(self, node_id: str, athlete_name: str):
        if self._state not in (RaceState.IDLE, RaceState.READY):
            raise ValueError(f"Cannot register nodes in state {self._state}")
        self._registered_nodes[node_id] = athlete_name

    def update_active_node(self, node_id: str, equipment_type: str):
        self._active_nodes[node_id] = equipment_type

    def get_station_equipment_type(self, station_number: int) -> str:
        node_id = self._stations.get(station_number)
        if not node_id:
            return "unknown"
        return self._active_nodes.get(node_id, "unknown")

    def ensure_running_node_registered(self, node_id: str):
        if node_id in self.get_registered_nodes():
            return

        athlete_name = f"Athlete {node_id}"
        self._registered_nodes[node_id] = athlete_name
        if node_id not in self._progress:
            self._progress[node_id] = {
                "node_id": node_id,
                "athlete_name": athlete_name,
                "station_number": None,
                "team_name": None,
                "avatar_url": None,
                "distance_m": 0.0,
                "elapsed_time_ms": 0,
                "instantaneous_speed_kph": 0.0,
                "progress_percent": 0.0,
                "calories": 0.0,
                "power_watts": 0,
                "max_power_watts": 0,
                "finished_time_ms": None,
            }

    def ingest_telemetry(self, payload: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Any]]]:
        node_id = payload.get("node_id")
        if not node_id:
            return None

        equipment_type = payload.get("equipment_type", "unknown")
        self.update_active_node(node_id, equipment_type)

        if self.get_state() != RaceState.RUNNING:
            return None

        self.ensure_running_node_registered(node_id)
        return self.update_telemetry(payload)

    def assign_station(self, station_number: int, node_id: Optional[str]):
        if self._state == RaceState.RUNNING:
            raise ValueError("Cannot assign stations while race is running")

        # Unassign if node_id is empty or None
        if not node_id:
            if station_number in self._stations:
                del self._stations[station_number]
            if station_number in self._station_registrations:
                del self._station_registrations[station_number]
            if station_number in self._station_teams:
                del self._station_teams[station_number]
            if station_number in self._station_has_avatar:
                del self._station_has_avatar[station_number]
            self._persist_settings()
            return

        # Ensure node_id is only assigned to one station at a time
        stations_to_remove = [sn for sn, nid in self._stations.items() if nid == node_id]
        for sn in stations_to_remove:
            del self._stations[sn]

        self._stations[station_number] = node_id
        self._persist_settings()

    def register_athlete(self, station_number: int, athlete_name: str, team_name: Optional[str] = None, has_avatar: bool = False):
        if self._state not in (RaceState.IDLE, RaceState.READY):
            raise ValueError(f"Cannot register athletes in state {self._state}")
        self._station_registrations[station_number] = athlete_name
        self._station_teams[station_number] = team_name
        self._station_has_avatar[station_number] = has_avatar

    def get_stations_status(self) -> dict:
        assigned_nodes = set(self._stations.values())
        unassigned_nodes = [nid for nid in self._active_nodes if nid not in assigned_nodes]

        stations_data = {}
        for sn, nid in self._stations.items():
            eq_type = self._active_nodes.get(nid, "unknown")
            ath_name = self._station_registrations.get(sn)
            stations_data[sn] = {
                "node_id": nid,
                "equipment_type": eq_type,
                "athlete_name": ath_name,
                "team_name": self._station_teams.get(sn),
                "has_avatar": self._station_has_avatar.get(sn, False),
            }

        # Include stations that have athlete registrations but no bound node_id
        for sn in self._station_registrations:
            if sn not in stations_data:
                stations_data[sn] = {
                    "node_id": None,
                    "equipment_type": None,
                    "athlete_name": self._station_registrations[sn],
                    "team_name": self._station_teams.get(sn),
                    "has_avatar": self._station_has_avatar.get(sn, False),
                }

        return {
            "stations": stations_data,
            "unassigned_nodes": unassigned_nodes
        }

    def start_race(self):
        if self._state != RaceState.READY:
            raise ValueError("Race must be in READY state to start")
        self._state = RaceState.RUNNING
        import time
        self._start_time_epoch_ms = int(time.time() * 1000)
        self._end_time_epoch_ms = None
        
        # Initialize progress
        self._progress = {}
        
        # 1. Initialize from legacy registered_nodes
        for node_id, athlete_name in self._registered_nodes.items():
            station_number = None
            for sn, nid in self._stations.items():
                if nid == node_id:
                    station_number = sn
                    break
            self._progress[node_id] = {
                "node_id": node_id,
                "athlete_name": athlete_name,
                "station_number": station_number,
                "team_name": None,
                "avatar_url": None,
                "distance_m": 0.0,
                "elapsed_time_ms": 0,
                "instantaneous_speed_kph": 0.0,
                "progress_percent": 0.0,
                "calories": 0.0,
                "power_watts": 0,
                "max_power_watts": 0,
                "finished_time_ms": None,
            }
            
        # 2. Initialize from station registrations
        for station_number, athlete_name in self._station_registrations.items():
            node_id = self._stations.get(station_number)
            key = node_id if node_id else f"station-{station_number}"
            if key not in self._progress:
                team_name = self._station_teams.get(station_number)
                has_avatar = self._station_has_avatar.get(station_number, False)
                import time
                avatar_url = f"/static/avatars/station_{station_number}.webp?t={int(time.time())}" if has_avatar else None
                
                self._progress[key] = {
                    "node_id": node_id or key,
                    "athlete_name": athlete_name,
                    "station_number": station_number,
                    "team_name": team_name,
                    "avatar_url": avatar_url,
                    "distance_m": 0.0,
                    "elapsed_time_ms": 0,
                    "instantaneous_speed_kph": 0.0,
                    "progress_percent": 0.0,
                    "calories": 0.0,
                    "power_watts": 0,
                    "max_power_watts": 0,
                    "finished_time_ms": None,
                }

    def stop_race(self):
        if self._state == RaceState.STOPPED:
            return
        if self._state != RaceState.RUNNING:
            raise ValueError("Race is not running")
        self._state = RaceState.STOPPED
        import time
        self._end_time_epoch_ms = int(time.time() * 1000)

    def close_race(self):
        self.stop_race()

    def reset_race(self):
        self._state = RaceState.IDLE
        self._config = None
        self._start_time_epoch_ms = None
        self._end_time_epoch_ms = None
        self._registered_nodes.clear()
        self._progress.clear()
        # Clean current athlete registrations but keep hardware station mapping
        self._station_registrations.clear()
        self._station_teams.clear()
        self._station_has_avatar.clear()
        self._active_nodes.clear()

    def update_telemetry(self, payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        if self._state == RaceState.STOPPED:
            return self._progress
        if self._state != RaceState.RUNNING:
            raise ValueError("Telemetry can only be updated during a running race")

        node_id = payload.get("node_id")
        if not node_id:
            return self._progress

        # Auto-discover node and type
        eq_type = payload.get("equipment_type", "unknown")
        self.update_active_node(node_id, eq_type)

        # Retrieve athlete name and station number
        station_number = None
        for sn, nid in self._stations.items():
            if nid == node_id:
                station_number = sn
                break

        team_name = None
        avatar_url = None
        if station_number is not None:
            athlete_name = self._station_registrations.get(station_number, f"Athlete {node_id}")
            team_name = self._station_teams.get(station_number)
            has_avatar = self._station_has_avatar.get(station_number, False)
            import time
            avatar_url = f"/static/avatars/station_{station_number}.webp?t={int(time.time())}" if has_avatar else None
        else:
            athlete_name = self._registered_nodes.get(node_id, f"Athlete {node_id}")

        # Clean up placeholder key if device is dynamically bound/discovered
        if station_number is not None:
            placeholder_key = f"station-{station_number}"
            if placeholder_key in self._progress:
                del self._progress[placeholder_key]

        prev_progress = self._progress.get(node_id, {})
        prev_finished_time = prev_progress.get("finished_time_ms")
        prev_max_power = prev_progress.get("max_power_watts", 0)

        distance_m = self._session_metric_value(
            prev_progress,
            payload,
            total_field="distance_m",
            delta_field="delta_distance_m",
        )
        elapsed_time_ms = self._elapsed_time_ms(payload)
        speed = payload.get("instantaneous_speed_kph", 0.0)
        power_watts = int(payload.get("power_watts", 0))
        calories = self._session_metric_value(
            prev_progress,
            payload,
            total_field="calories",
            delta_field="delta_energy_kcal",
        )

        if calories is None:
            calories = (power_watts * (elapsed_time_ms / 1000.0)) / 1000.0

        if prev_finished_time is not None:
            distance_m = prev_progress.get("distance_m", distance_m)
            elapsed_time_ms = prev_progress.get("elapsed_time_ms", elapsed_time_ms)
            calories = prev_progress.get("calories", calories)
            speed = 0.0
            power_watts = 0

        max_power_watts = max(prev_max_power, power_watts)

        # Calculate progress percent
        progress_percent = 0.0
        if self._config:
            if self._config.race_type == "distance" and self._config.target_value > 0:
                progress_percent = (distance_m / self._config.target_value) * 100.0
            elif self._config.race_type == "calories" and self._config.target_value > 0:
                progress_percent = (calories / self._config.target_value) * 100.0
            elif self._config.race_type in ("time", "max_power", "watts") and self._config.duration_sec > 0:
                progress_percent = (
                    elapsed_time_ms / (self._config.duration_sec * 1000.0)
                ) * 100.0

        finished_time_ms = prev_finished_time
        if progress_percent >= 100.0 and finished_time_ms is None:
            finished_time_ms = elapsed_time_ms

        # Update metrics
        self._progress[node_id] = {
            "node_id": node_id,
            "athlete_name": athlete_name,
            "station_number": station_number,
            "team_name": team_name,
            "avatar_url": avatar_url,
            "distance_m": distance_m,
            "elapsed_time_ms": elapsed_time_ms,
            "instantaneous_speed_kph": speed,
            "progress_percent": round(progress_percent, 2),
            "calories": round(calories, 1),
            "power_watts": power_watts,
            "max_power_watts": max_power_watts,
            "finished_time_ms": finished_time_ms,
        }

        # Check if all participants have finished the race
        if self._progress:
            all_finished = True
            for nid, p in self._progress.items():
                if nid.startswith("station-"):
                    continue
                if self._config.race_type in ("distance", "calories"):
                    if p.get("finished_time_ms") is None:
                        all_finished = False
                        break
                elif self._config.race_type in ("time", "max_power", "watts"):
                    if p.get("elapsed_time_ms", 0) < (self._config.duration_sec * 1000):
                        all_finished = False
                        break
            if all_finished:
                self._state = RaceState.STOPPED
                import time
                self._end_time_epoch_ms = int(time.time() * 1000)

        return self._progress

    def _session_metric_value(
        self,
        prev_progress: Dict[str, Any],
        payload: Dict[str, Any],
        *,
        total_field: str,
        delta_field: str,
    ) -> float | None:
        if delta_field in payload and payload.get(delta_field) is not None:
            previous = self._metric_number(prev_progress.get(total_field))
            delta = max(0.0, self._metric_number(payload.get(delta_field)))
            return previous + delta
        value = payload.get(total_field)
        return None if value is None else self._metric_number(value)

    def _elapsed_time_ms(self, payload: Dict[str, Any]) -> int:
        elapsed_time_ms = int(self._metric_number(payload.get("elapsed_time_ms")))
        if elapsed_time_ms > 0:
            return elapsed_time_ms
        timestamp_ms = payload.get("timestamp_epoch_ms")
        if self._start_time_epoch_ms and timestamp_ms:
            return max(0, int(self._metric_number(timestamp_ms)) - self._start_time_epoch_ms)
        import time
        if self._start_time_epoch_ms:
            return max(0, int(time.time() * 1000) - self._start_time_epoch_ms)
        return 0
