import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any


DEFAULT_EDGE_MONITOR_PATH = "data/edge_monitor.jsonl"


class EdgeEventLog:
    def __init__(
        self,
        path: str | Path,
        max_bytes: int = 2_000_000,
        trim_to_lines: int = 500,
        max_payload_chars: int = 4000,
    ):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.trim_to_lines = trim_to_lines
        self.max_payload_chars = max_payload_chars

    @classmethod
    def from_env(cls):
        return cls(os.getenv("FITRACE_EDGE_MONITOR_PATH", DEFAULT_EDGE_MONITOR_PATH))

    def record(
        self,
        source: str,
        direction: str,
        *,
        channel: str | None = None,
        topic: str | None = None,
        message: str | None = None,
        payload: Any = None,
        parsed: Any = None,
    ):
        event = {
            "timestamp_epoch_ms": int(time.time() * 1000),
            "source": source,
            "direction": direction,
            "channel": channel,
            "topic": topic,
            "message": message,
            "payload": self._limit(payload),
            "parsed": self._limit(parsed),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._trim_if_needed()

    def list_events(self, limit: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        limit = max(1, min(limit, 500))
        lines: deque[str] = deque(maxlen=limit)
        with self.path.open(encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    lines.append(line)

        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _trim_if_needed(self):
        try:
            if not self.path.exists() or self.path.stat().st_size <= self.max_bytes:
                return
            with self.path.open(encoding="utf-8") as file:
                lines = deque(file, maxlen=self.trim_to_lines)
            with self.path.open("w", encoding="utf-8") as file:
                file.writelines(lines)
        except OSError:
            return

    def _limit(self, value: Any) -> Any:
        if value is None:
            return None
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded) <= self.max_payload_chars:
            return value
        return {
            "truncated": True,
            "text": encoded[: self.max_payload_chars],
        }
