"""Roster import and heat queue for the 500 m sprint event.

Pure logic module: no FastAPI, no direct file I/O. `parse_roster_csv` takes
CSV text and returns data; `RosterManager` takes an injected store (any
object exposing `load()`/`save()` -- see `race_settings_store.
RaceSettingsStore`) and turns it into a small in-memory queue with heat
formation, absence, requeue, and walk-in operations. Heat size is a caller
concern (the number of stations that currently have a node assigned lives
in RaceManager, not here) -- every heat-forming call takes the list of
station numbers currently available as a plain argument.
"""

import csv
import uuid
from typing import Any, Optional

MAX_NAME_LENGTH = 80
MAX_TEAM_LENGTH = 80

_BOM = "﻿"

_NAME_ALIASES = {"name", "姓名", "名字"}
_DIVISION_ALIASES = {"division", "組別", "性別"}
_TEAM_ALIASES = {"team", "隊伍", "隊名"}

_MEN_VALUES = {"men", "male", "m", "男", "男子", "男子組"}
_WOMEN_VALUES = {"women", "female", "f", "女", "女子", "女子組"}

_STATUSES = ("pending", "loaded", "done", "absent")


def _new_id() -> str:
    return uuid.uuid4().hex


def _row_is_blank(row: list[str]) -> bool:
    return not any(cell.strip() for cell in row)


def parse_roster_csv(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse roster CSV text.

    Returns (entries, errors). On any row error, entries is [] and errors
    lists every offending row as {"row": <1-based line number>, "message":
    str}. On success, entries are all "pending", ordered by file order
    (0-based `order`).
    """
    if text.startswith(_BOM):
        text = text[len(_BOM) :]

    rows = list(csv.reader(text.splitlines()))

    header_row_index = None
    for i, row in enumerate(rows):
        if not _row_is_blank(row):
            header_row_index = i
            break

    if header_row_index is None:
        return [], [{"row": 1, "message": "CSV has no header row"}]

    header_map: dict[int, str] = {}
    for col_idx, cell in enumerate(rows[header_row_index]):
        key = cell.strip().lower()
        if key in _NAME_ALIASES:
            header_map[col_idx] = "name"
        elif key in _DIVISION_ALIASES:
            header_map[col_idx] = "division"
        elif key in _TEAM_ALIASES:
            header_map[col_idx] = "team"

    if "name" not in header_map.values():
        return [], [
            {
                "row": header_row_index + 1,
                "message": "Missing required column: name",
            }
        ]

    errors: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []

    for i in range(header_row_index + 1, len(rows)):
        row = rows[i]
        line_no = i + 1
        if _row_is_blank(row):
            continue

        raw: dict[str, str] = {}
        for col_idx, cell in enumerate(row):
            field = header_map.get(col_idx)
            if field:
                raw[field] = cell

        name = (raw.get("name") or "").strip()
        if not name:
            errors.append({"row": line_no, "message": "Missing name"})
            continue
        if len(name) > MAX_NAME_LENGTH:
            errors.append(
                {
                    "row": line_no,
                    "message": f"Name too long (max {MAX_NAME_LENGTH} characters)",
                }
            )
            continue

        division_cell = (raw.get("division") or "").strip()
        if not division_cell:
            division = None
        else:
            key = division_cell.lower()
            if key in _MEN_VALUES:
                division = "men"
            elif key in _WOMEN_VALUES:
                division = "women"
            else:
                errors.append(
                    {"row": line_no, "message": f"Invalid division: {division_cell}"}
                )
                continue

        team_cell = (raw.get("team") or "").strip()
        if len(team_cell) > MAX_TEAM_LENGTH:
            errors.append(
                {
                    "row": line_no,
                    "message": f"Team name too long (max {MAX_TEAM_LENGTH} characters)",
                }
            )
            continue
        team = team_cell or None

        entries.append(
            {
                "id": _new_id(),
                "name": name,
                "division": division,
                "team": team,
                "status": "pending",
                "order": len(entries),
                "station_number": None,
                "started": False,
            }
        )

    if errors:
        return [], errors
    return entries, []


class RosterManager:
    """In-memory roster queue backed by an injected atomic JSON store."""

    def __init__(self, store):
        self._store = store
        self._entries: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        data = self._store.load()
        if not data or not isinstance(data.get("entries"), list):
            return []
        loaded = []
        for raw in data["entries"]:
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            # Backward-compatible default for a roster.json written before
            # the "started" flag existed -- never treat an old file's
            # entries as having raced.
            entry.setdefault("started", False)
            loaded.append(entry)
        return loaded

    def _persist(self) -> None:
        self._store.save({"entries": self._entries})

    def _find(self, entry_id: str) -> Optional[dict[str, Any]]:
        for entry in self._entries:
            if entry["id"] == entry_id:
                return entry
        return None

    def entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._entries]

    def clear(self) -> None:
        self._entries = []
        self._persist()

    def import_csv(self, text: str) -> list[dict[str, Any]]:
        entries, errors = parse_roster_csv(text)
        if errors:
            return errors
        self._entries = entries
        self._persist()
        return []

    def _next_order(self) -> int:
        if not self._entries:
            return 0
        return max(entry["order"] for entry in self._entries) + 1

    def mark_absent(self, entry_id: str) -> None:
        entry = self._find(entry_id)
        if entry is None:
            raise ValueError("Unknown roster entry")
        if entry["status"] != "pending":
            raise ValueError("Only a pending entry can be marked absent")
        entry["status"] = "absent"
        self._persist()

    def requeue(self, entry_id: str) -> None:
        entry = self._find(entry_id)
        if entry is None:
            raise ValueError("Unknown roster entry")
        if entry["status"] not in ("absent", "done"):
            raise ValueError("Only an absent or done entry can be requeued")
        entry["status"] = "pending"
        entry["order"] = self._next_order()
        entry["station_number"] = None
        entry["started"] = False
        self._persist()

    def mark_current_heat_started(self) -> None:
        """Mark every currently "loaded" entry as started -- called right
        after race_manager.start_race() actually succeeds. This is how the
        next-heat guard tells a heat that raced from one that's still
        sitting loaded and unraced, WITHOUT relying on the race's current
        state: the normal flow races a heat to STOPPED and then presses
        Reset Race (state back to IDLE) before loading the next heat, and
        that reset must not make a raced heat look unraced again. A no-op
        (no persist) when nothing is currently loaded.
        """
        changed = False
        for entry in self._entries:
            if entry["status"] == "loaded" and not entry.get("started"):
                entry["started"] = True
                changed = True
        if changed:
            self._persist()

    def add_walk_in(
        self, name: str, division: Optional[str], team: Optional[str]
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Name is required")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"Name too long (max {MAX_NAME_LENGTH} characters)")
        if division not in (None, "men", "women"):
            raise ValueError("Invalid division")
        team = (team or "").strip() or None
        if team and len(team) > MAX_TEAM_LENGTH:
            raise ValueError(f"Team name too long (max {MAX_TEAM_LENGTH} characters)")

        entry = {
            "id": _new_id(),
            "name": name,
            "division": division,
            "team": team,
            "status": "pending",
            "order": self._next_order(),
            "station_number": None,
            "started": False,
        }
        self._entries.append(entry)
        self._persist()
        return dict(entry)

    def load_next_heat(self, station_numbers: list[int]) -> list[dict[str, Any]]:
        """Move the currently "loaded" heat to "done" and load the next
        pending entries onto `station_numbers`.

        Builds the result on a working copy and only swaps it into
        `self._entries` (and persists) once the whole operation is known to
        succeed -- a raise (no stations, or nothing left pending) must leave
        both the in-memory entries and the persisted file exactly as they
        were, never a half-applied loaded->done transition sitting in
        memory with the "loaded" file still on disk.
        """
        if not station_numbers:
            raise ValueError("assign stations first")

        working = [dict(entry) for entry in self._entries]

        for entry in working:
            if entry["status"] == "loaded":
                entry["status"] = "done"
                entry["station_number"] = None
                # Dropped, not carried into history: "started" only means
                # anything for the CURRENTLY loaded heat -- a done entry's
                # history is its status, not this flag.
                entry["started"] = False

        pending = sorted(
            (entry for entry in working if entry["status"] == "pending"),
            key=lambda entry: entry["order"],
        )
        if not pending:
            raise ValueError("roster exhausted")

        stations_sorted = sorted(station_numbers)
        chosen = pending[: len(stations_sorted)]
        loaded: list[dict[str, Any]] = []
        for entry, station_number in zip(chosen, stations_sorted):
            entry["status"] = "loaded"
            entry["station_number"] = station_number
            entry["started"] = False
            loaded.append(dict(entry))

        self._entries = working
        self._persist()
        return loaded

    def summary(self, station_numbers: list[int]) -> dict[str, Any]:
        heat_size = len(station_numbers)
        counts = {status: 0 for status in _STATUSES}
        for entry in self._entries:
            counts[entry["status"]] += 1

        current_heat = sorted(
            (dict(entry) for entry in self._entries if entry["status"] == "loaded"),
            key=lambda entry: entry.get("station_number") or 0,
        )
        pending_sorted = sorted(
            (entry for entry in self._entries if entry["status"] == "pending"),
            key=lambda entry: entry["order"],
        )
        next_heat = [dict(entry) for entry in pending_sorted[:heat_size]]

        return {
            "entries": self.entries(),
            "heat_size": heat_size,
            "current_heat": current_heat,
            "next_heat": next_heat,
            "counts": counts,
        }
