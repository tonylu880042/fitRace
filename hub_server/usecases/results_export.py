"""Build the race-results CSV export.

Pure CSV building (rows -> text) -- no FastAPI imports. The
label/translation function and the local-time formatter are injected so this
is unit-testable without a running app or dependence on the system
timezone (see hub_server/infrastructure/fastapi/app.py for the real wiring).
"""

import csv
import io
from typing import Any, Callable, Iterator, Optional

from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RESULTS_READ_LIMIT, RaceResultsQuery

CSV_BOM = "﻿"

# A cell starting with any of these can be interpreted as a spreadsheet
# formula by Excel/Sheets on open. Only text that ultimately comes from an
# athlete/roster CSV (names, team names) crosses that trust boundary, so
# only those cells are defused -- a leading "'" forces spreadsheet software
# to treat the cell as plain text.
_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_HEADER_KEYS = [
    "export.header_race_start",
    "export.header_race_type",
    "export.header_target",
    "export.header_mode",
    "export.header_rank",
    "export.header_name",
    "export.header_division",
    "export.header_team",
    "export.header_station",
    "export.header_time_sec",
    "export.header_status",
    "export.header_distance_m",
    "export.header_calories",
    "export.header_max_power_w",
    "export.header_relay_members",
    "export.header_relay_splits",
]

_MODE_LOCALE_KEYS = {
    "individual": "stage.individual",
    "team": "stage.team",
    "relay": "stage.relay",
}

_DIVISION_LOCALE_KEYS = {
    "men": "record_wall.division_men",
    "women": "record_wall.division_women",
}

# Only the two locales the export endpoint actually supports (see
# hub_server/infrastructure/fastapi/app.py) -- matches the anonymous-name
# fallback wording used on the public results pages (results.html's
# athleteDisplayName), not the six-locale locale-file system.
_STATION_WORD = {"zh-TW": "站位", "en-US": "Station"}
_ATHLETE_WORD = {"zh-TW": "選手", "en-US": "Athlete"}

_DNF_RACE_TYPES = ("distance", "calories")


def _sanitize(value: str) -> str:
    if value and value[0] in _INJECTION_PREFIXES:
        return "'" + value
    return value


def _seconds(ms: Optional[float]) -> str:
    if ms is None:
        return ""
    return f"{ms / 1000:.3f}"


def _decimal(value: Optional[float], places: int) -> str:
    if value is None:
        return ""
    return f"{float(value):.{places}f}"


def _plain_number(value: Optional[float]) -> str:
    if value is None:
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _display_name(row: dict[str, Any], lang: str) -> str:
    """Anonymous-finisher fallback, mirroring results.html's
    athleteDisplayName: a real chosen name wins, otherwise a station label,
    otherwise a generic "Athlete" placeholder. For team/relay rows the
    leaderboard already carries the team name in athlete_name (see
    race_manager.py), so no separate team-vs-athlete branching is needed
    here.
    """
    name = row.get("athlete_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    station_number = row.get("station_number")
    if station_number is not None:
        word = _STATION_WORD.get(lang, _STATION_WORD["en-US"])
        return f"{word} {station_number}"
    return _ATHLETE_WORD.get(lang, _ATHLETE_WORD["en-US"])


def _iter_export_races(
    store: RaceResultStore, query: RaceResultsQuery
) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield (race, target_label) for every stored race, ordered by start
    time ascending. `race` is the same shape RaceResultsQuery.get_race()
    returns elsewhere (so ranks/tokens match the results pages exactly);
    `target_label` reuses RaceResultsQuery's own category-label logic so the
    exported label matches the record wall / results pages too.
    """
    entries = []
    for record in store.list_results(limit=RESULTS_READ_LIMIT):
        if not isinstance(record, dict):
            continue
        result_id = record.get("result_id")
        snapshot = record.get("snapshot")
        if not result_id or not isinstance(snapshot, dict):
            continue
        config = snapshot.get("config")
        config = config if isinstance(config, dict) else {}
        race_type = config.get("race_type")
        label = RaceResultsQuery._category_label(race_type, config) or ""
        race = query.get_race(result_id)
        if race is None:
            continue
        entries.append((race.get("start_time_epoch_ms") or 0, race, label))

    entries.sort(key=lambda item: item[0])
    for _, race, label in entries:
        yield race, label


def _build_row(
    race: dict[str, Any],
    label: str,
    participant: dict[str, Any],
    translate: Callable[[str], str],
    format_local_datetime: Callable[[int], str],
    lang: str,
) -> list[str]:
    race_type = race.get("race_type")
    competition_mode = race.get("competition_mode")
    mode_key = _MODE_LOCALE_KEYS.get(competition_mode)
    mode_label = translate(mode_key) if mode_key else (competition_mode or "")

    division = participant.get("division")
    division_key = _DIVISION_LOCALE_KEYS.get(division)
    division_label = translate(division_key) if division_key else ""

    finished_time_ms = participant.get("finished_time_ms")
    status = ""
    if race_type in _DNF_RACE_TYPES and finished_time_ms is None:
        status = translate("record_wall.dnf")

    station_number = participant.get("station_number")
    station_label = "" if station_number is None else str(station_number)

    relay_members = [
        _sanitize(str(member)) for member in (participant.get("relay_members") or [])
    ]
    relay_splits = [
        _seconds(split) for split in (participant.get("relay_splits") or [])
    ]

    start_time_epoch_ms = race.get("start_time_epoch_ms")
    race_start = (
        format_local_datetime(start_time_epoch_ms)
        if start_time_epoch_ms is not None
        else ""
    )
    race_type_label = translate(f"race_type.{race_type}") if race_type else ""

    return [
        race_start,
        race_type_label,
        label,
        mode_label,
        str(participant.get("rank", "")),
        _sanitize(_display_name(participant, lang)),
        division_label,
        _sanitize(participant.get("team_name") or ""),
        station_label,
        _seconds(finished_time_ms),
        status,
        _decimal(participant.get("distance_m"), 1),
        _decimal(participant.get("calories"), 1),
        _plain_number(participant.get("max_power_watts")),
        " → ".join(relay_members),
        " / ".join(relay_splits),
    ]


def build_results_csv(
    store: RaceResultStore,
    query: RaceResultsQuery,
    translate: Callable[[str], str],
    format_local_datetime: Callable[[int], str],
    lang: str = "zh-TW",
) -> str:
    """Render every stored race result as CSV text (UTF-8, with a leading
    BOM so Excel renders non-ASCII names correctly). One row per
    participant (athlete or relay team), ordered by race start time
    ascending then rank within the race.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([translate(key) for key in _HEADER_KEYS])

    for race, label in _iter_export_races(store, query):
        for participant in race.get("results", []):
            writer.writerow(
                _build_row(
                    race, label, participant, translate, format_local_datetime, lang
                )
            )

    return CSV_BOM + buffer.getvalue()
