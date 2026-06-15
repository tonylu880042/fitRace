from typing import Dict, Any, Optional
from hub_server.domain.models import RaceState, RaceConfig


class RaceManager:
    def __init__(self):
        self._state: RaceState = RaceState.IDLE
        self._config: Optional[RaceConfig] = None
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

    def get_state(self) -> RaceState:
        return self._state

    def get_config(self) -> Optional[RaceConfig]:
        return self._config

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

    def register_node(self, node_id: str, athlete_name: str):
        if self._state not in (RaceState.IDLE, RaceState.READY):
            raise ValueError(f"Cannot register nodes in state {self._state}")
        self._registered_nodes[node_id] = athlete_name

    def update_active_node(self, node_id: str, equipment_type: str):
        self._active_nodes[node_id] = equipment_type

    def assign_station(self, station_number: int, node_id: Optional[str]):
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
            return

        # Ensure node_id is only assigned to one station at a time
        stations_to_remove = [sn for sn, nid in self._stations.items() if nid == node_id]
        for sn in stations_to_remove:
            del self._stations[sn]

        self._stations[station_number] = node_id

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
            
        # 2. Initialize from station assignments
        for station_number, node_id in self._stations.items():
            if node_id not in self._progress:
                athlete_name = self._station_registrations.get(station_number, f"Athlete {node_id}")
                team_name = self._station_teams.get(station_number)
                has_avatar = self._station_has_avatar.get(station_number, False)
                import time
                avatar_url = f"/static/avatars/station_{station_number}.webp?t={int(time.time())}" if has_avatar else None
                
                self._progress[node_id] = {
                    "node_id": node_id,
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

        prev_progress = self._progress.get(node_id, {})
        prev_finished_time = prev_progress.get("finished_time_ms")
        prev_max_power = prev_progress.get("max_power_watts", 0)

        distance_m = payload.get("distance_m", 0.0)
        elapsed_time_ms = payload.get("elapsed_time_ms", 0)
        speed = payload.get("instantaneous_speed_kph", 0.0)
        power_watts = int(payload.get("power_watts", 0))
        calories = payload.get("calories")

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

