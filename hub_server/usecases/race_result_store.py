import json
import time
from pathlib import Path
from typing import Any


class RaceResultStore:
    def __init__(self, path: str | Path, session_mode: str = "race"):
        self._path = Path(path)
        self._saved_keys: set[str] = set()
        # Which session kind this instance files: "race" (the historical,
        # default behaviour every existing caller relies on) or "class". A
        # single class parameterised this way -- rather than a second,
        # copy-pasted store class -- keeps the append-only file format, the
        # dedup-by-result_id logic, and list_results() identical for both
        # kinds; only the filter below differs.
        self._session_mode = session_mode

    @property
    def path(self) -> Path:
        return self._path

    def save_finished_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        if snapshot.get("state") != "STOPPED":
            return None
        # Snapshots written before class mode existed have no session_mode
        # key at all -- absent must mean "race", not "class", for either
        # store: a race-mode store must keep filing those old snapshots,
        # and a class-mode store must keep rejecting them.
        snapshot_session_mode = snapshot.get("session_mode") or "race"
        if snapshot_session_mode != self._session_mode:
            return None
        result_key = self._result_key(snapshot)
        if result_key in self._saved_keys or self._key_exists(result_key):
            self._saved_keys.add(result_key)
            return None

        record = {
            "result_id": result_key,
            "saved_epoch_ms": int(time.time() * 1000),
            "snapshot": snapshot,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
        self._saved_keys.add(result_key)
        return record

    def list_results(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records[-max(1, limit) :]

    def _key_exists(self, result_key: str) -> bool:
        if not self._path.exists():
            return False
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("result_id") == result_key:
                    return True
        return False

    def _result_key(self, snapshot: dict[str, Any]) -> str:
        start = snapshot.get("start_time_epoch_ms") or "no-start"
        end = snapshot.get("end_time_epoch_ms") or "no-end"
        config = snapshot.get("config") or {}
        race_type = config.get("race_type") or "unknown"
        return f"{start}-{end}-{race_type}"
