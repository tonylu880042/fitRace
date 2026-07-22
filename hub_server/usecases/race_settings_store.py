import json
from pathlib import Path
from typing import Any, Optional


class RaceSettingsStore:
    """Persists operator-configured race settings to a JSON file so they
    survive a hub restart. Only durable settings are stored — not the
    transient state of a race in progress (see RaceManager)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Optional[dict[str, Any]]:
        if not self._path.exists():
            return None
        try:
            with self._path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(self._path)  # atomic on POSIX — no half-written file
