import hashlib
import itertools
from typing import Any, Optional

from hub_server.usecases.race_result_store import RaceResultStore

# Generous upper bound so we effectively read the whole jsonl history without
# the store having to grow an "unlimited" mode.
RESULTS_READ_LIMIT = 500

_TARGET_RACE_TYPES = ("distance", "calories")
_TIME_BOXED_RACE_TYPES = ("time", "watts")


def _make_token(result_id: str, node_id: str) -> str:
    """Deterministic short public id for an athlete's result row.

    Race results are public leaderboard data (no PII beyond a display name
    the athlete chose at check-in), so this only needs to be unguessable
    enough to avoid trivial enumeration, not cryptographically secret.
    """
    return hashlib.sha1(f"{result_id}:{node_id}".encode()).hexdigest()[:12]


def _as_number(value: Any) -> float:
    return value if isinstance(value, (int, float)) else 0


def _format_number(value: float) -> str:
    """Render a numeric target/duration without a noisy trailing '.0'."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


class RaceResultsQuery:
    """Read-only query layer over the append-only race results jsonl store."""

    def __init__(self, store: RaceResultStore):
        self._store = store

    def list_races(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            summary for summary, _, _ in itertools.islice(self._iter_races(), limit)
        ]

    def get_race(self, result_id: str) -> Optional[dict[str, Any]]:
        for summary, ranked_rows, team_leaderboard in self._iter_races():
            if summary["result_id"] == result_id:
                return {
                    **summary,
                    "results": ranked_rows,
                    "team_leaderboard": team_leaderboard,
                }
        return None

    def get_records(self) -> dict[str, Any]:
        """Best-of leaderboard per race category, most-recently-contested first.

        A "category" is (race_type, target label, division, relay_legs)
        e.g. ("distance", "1000 m", None, None) or ("distance", "500 m",
        "women", None). relay_legs is None for every non-relay race, so a
        relay race never mixes into the same category as an individual race
        at the same distance -- a 1000 m 4-leg relay and a 1000 m
        individual race both label "1000 m" but must never share a record
        slate. Entries are the top 3 rows across all stored races in that
        category, ranked by the metric that matters for the race type (see
        `_record_value`/`_top_three`).
        """
        categories: dict[tuple[str, str, Any, Any], dict[str, Any]] = {}

        # Newest-first, so the first race we see for a category is also the
        # most recently contested one -- dict insertion order then gives us
        # the required "most-recently-contested category first" ordering for
        # free, with no separate timestamp sort needed.
        for record in self._load_records():
            if not isinstance(record, dict):
                continue
            snapshot = record.get("snapshot")
            if not isinstance(snapshot, dict):
                continue
            config = snapshot.get("config")
            config = config if isinstance(config, dict) else {}
            race_type = config.get("race_type")
            label = self._category_label(race_type, config)
            if label is None:
                # ponytail: mixed races excluded until record categories
                # include equipment type
                continue
            relay_legs = config.get("relay_legs")

            end_time = snapshot.get("end_time_epoch_ms")
            leaderboard = snapshot.get("leaderboard")
            rows = self._participant_rows(
                leaderboard if isinstance(leaderboard, dict) else {}
            )

            for row in rows:
                value = self._record_value(race_type, row)
                if value is None:
                    continue
                division = row.get("division")
                bucket = categories.setdefault(
                    (race_type, label, division, relay_legs),
                    {
                        "race_type": race_type,
                        "label": label,
                        "division": division,
                        "relay_legs": relay_legs,
                        "rows": [],
                    },
                )
                bucket["rows"].append(
                    {
                        "athlete_name": row.get("athlete_name"),
                        "is_registered_name": row.get("is_registered_name"),
                        "team_name": row.get("team_name"),
                        "division": division,
                        "value": value,
                        "end_time_epoch_ms": end_time,
                    }
                )

        records = []
        for bucket in categories.values():
            entries = self._top_three(bucket["rows"], bucket["race_type"])
            if not entries:
                continue
            records.append(
                {
                    "race_type": bucket["race_type"],
                    "label": bucket["label"],
                    "division": bucket["division"],
                    "relay_legs": bucket["relay_legs"],
                    "entries": entries,
                }
            )
        return {"records": records}

    def get_standings(
        self, event_start_epoch_ms: Optional[float] = None
    ) -> dict[str, Any]:
        """One combined ranking across every heat of the current event.

        Several heats run on the same couple of machines (e.g. 3 relay
        heats x 2 teams) are each saved as their own race in the results
        jsonl -- get_records() only ever surfaces the top 3 of the most
        recently contested category. This instead scopes to (race_type,
        label, relay_legs) of the MOST RECENTLY stored race ONLY -- if
        that newest race is "mixed" (or otherwise not a well-formed
        category), standings are empty rather than reaching past it to an
        older category (see `_latest_standings_scope`) -- division is
        deliberately excluded from the scope key so men's/women's heats of
        the same event share one scope and are split back into sections
        afterwards -- and ranks EVERY participant row from EVERY stored
        race in that scope, with no truncation.

        `event_start_epoch_ms` draws the "current event" boundary an
        operator sets via Game Admin's "Start new event" action (see
        RaceManager.start_new_event): when given, a race whose
        start_time_epoch_ms is missing or falls before the boundary is
        dropped BEFORE the "most recently stored race" scope is even
        picked -- so an old rehearsal race sharing the same (race_type,
        label, relay_legs) as today's heats never becomes the scope, and
        never contributes rows either. None (the default) keeps counting
        the entire history, unchanged from before this parameter existed.
        """
        empty: dict[str, Any] = {"race_type": None, "sections": [], "race_count": 0}

        records = self._records_since(event_start_epoch_ms)
        scope = self._latest_standings_scope(records)
        if scope is None:
            return empty
        race_type, label, relay_legs = scope

        combined_rows: list[dict[str, Any]] = []
        race_count = 0
        for record in records:
            config = self._record_config(record)
            if config is None:
                continue
            if not self._matches_scope(config, race_type, label, relay_legs):
                continue
            snapshot = record["snapshot"]
            race_count += 1
            race_start = snapshot.get("start_time_epoch_ms")
            leaderboard = snapshot.get("leaderboard")
            for row in self._participant_rows(
                leaderboard if isinstance(leaderboard, dict) else {}
            ):
                enriched = dict(row)
                enriched["race_start_epoch_ms"] = race_start
                combined_rows.append(enriched)

        if not combined_rows:
            return empty

        ordered = self._order_by_race_type(combined_rows, race_type)
        tagged = [
            (row, *self._standings_finished_value(race_type, row)) for row in ordered
        ]
        deduped = self._dedupe_best_row(tagged)

        sections_by_division: dict[Any, list[dict[str, Any]]] = {}
        for row, finished, value in deduped:
            division = row.get("division")
            sections_by_division.setdefault(division, []).append(
                {
                    "athlete_name": row.get("athlete_name"),
                    "team_name": row.get("team_name"),
                    "division": division,
                    "finished": finished,
                    "value": value,
                    "station_number": row.get("station_number"),
                    "relay_members": row.get("relay_members"),
                    "race_start_epoch_ms": row.get("race_start_epoch_ms"),
                }
            )

        sections = []
        for division in sorted(
            sections_by_division.keys(), key=self._division_sort_key
        ):
            entries = sections_by_division[division]
            ranked_rows = [
                {"rank": index, **entry} for index, entry in enumerate(entries, 1)
            ]
            sections.append({"division": division, "rows": ranked_rows})

        return {
            "race_type": race_type,
            "label": label,
            "relay_legs": relay_legs,
            "race_count": race_count,
            "sections": sections,
        }

    def get_athlete_result(self, token: str) -> Optional[dict[str, Any]]:
        for summary, ranked_rows, _ in self._iter_races():
            for row in ranked_rows:
                if row["token"] == token:
                    result = {
                        "race": summary,
                        "athlete": row,
                        "total_athletes": len(ranked_rows),
                    }
                    if summary.get("race_type") == "mixed":
                        result["total_athletes"], result["group"] = (
                            self._athlete_group_scope(row, ranked_rows, summary)
                        )
                    return result
        return None

    # -- internals -----------------------------------------------------

    @staticmethod
    def _athlete_group_scope(
        row: dict[str, Any],
        ranked_rows: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> tuple[Optional[int], Optional[dict[str, Any]]]:
        """(total_athletes, group summary) for one row of a mixed race --
        scoped to the row's own group, not the whole (multi-group) race.
        None/None when the row's equipment matched no group."""
        group_index = row.get("group_index")
        groups = summary.get("groups") or []
        if not isinstance(group_index, int) or not (0 <= group_index < len(groups)):
            return None, None
        total_athletes = sum(
            1 for r in ranked_rows if r.get("group_index") == group_index
        )
        return total_athletes, groups[group_index]

    def _iter_races(self):
        """Yield (summary, ranked_rows, team_leaderboard) newest-first,
        skipping any record that doesn't look like a well-formed result."""
        for record in self._load_records():
            summary = self._summarize(record)
            if summary is None:
                continue
            snapshot = record["snapshot"]
            leaderboard = snapshot.get("leaderboard")
            rows = self._participant_rows(
                leaderboard if isinstance(leaderboard, dict) else {}
            )
            if summary["race_type"] == "mixed":
                ranked_rows = self._rank_and_tag_mixed(
                    rows, summary.get("groups") or [], summary["result_id"]
                )
            else:
                ranked_rows = self._rank_and_tag(
                    rows, summary["race_type"], summary["result_id"]
                )
            yield summary, ranked_rows, snapshot.get("team_leaderboard")

    def _load_records(self) -> list[Any]:
        # The jsonl file is append-only in chronological order; reverse to
        # present newest-first without needing a separate timestamp sort.
        return list(reversed(self._store.list_results(limit=RESULTS_READ_LIMIT)))

    @staticmethod
    def _summarize(record: Any) -> Optional[dict[str, Any]]:
        if not isinstance(record, dict):
            return None
        result_id = record.get("result_id")
        snapshot = record.get("snapshot")
        if not result_id or not isinstance(snapshot, dict):
            return None
        config = snapshot.get("config")
        config = config if isinstance(config, dict) else {}
        leaderboard = snapshot.get("leaderboard")
        leaderboard = leaderboard if isinstance(leaderboard, dict) else {}
        athlete_count = sum(
            1 for row in leaderboard.values() if RaceResultsQuery._is_participant(row)
        )
        summary = {
            "result_id": result_id,
            "race_type": config.get("race_type"),
            "competition_mode": config.get("competition_mode"),
            "start_time_epoch_ms": snapshot.get("start_time_epoch_ms"),
            "end_time_epoch_ms": snapshot.get("end_time_epoch_ms"),
            "athlete_count": athlete_count,
        }
        if config.get("race_type") == "mixed":
            summary["groups"] = RaceResultsQuery._summarize_groups(config)
        return summary

    @staticmethod
    def _summarize_groups(config: dict[str, Any]) -> list[dict[str, Any]]:
        groups = config.get("groups")
        groups = groups if isinstance(groups, list) else []
        summaries = []
        for index, group in enumerate(groups):
            group = group if isinstance(group, dict) else {}
            race_type = group.get("race_type")
            summaries.append(
                {
                    "group_index": index,
                    "race_type": race_type,
                    "target_value": group.get("target_value"),
                    "duration_sec": group.get("duration_sec"),
                    "equipment_types": group.get("equipment_types"),
                    "label": RaceResultsQuery._category_label(race_type, group),
                }
            )
        return summaries

    @staticmethod
    def _is_participant(row: Any) -> bool:
        """A leaderboard row counts as a participant if it names someone,
        OR is anonymous-but-real (a real station, no name given).

        Anonymous participation (commit a2a397c) lets `athlete_name` be
        `None`: RegisterAthletePayload normalizes a blank/whitespace name to
        `None` before it ever reaches a leaderboard row, so a genuine
        anonymous finisher's row looks like `{"athlete_name": None,
        "station_number": 3, ...}`. Production code never emits
        `athlete_name == ""` -- that value only exists in older test
        fixtures as a stand-in for "not really a participant".

        Those two falsy values are therefore deliberately NOT equivalent
        here: `None` plus a real `station_number` is a person who chose not
        to give a name and must be kept; `""` (or any other falsy-but-not-
        None name) is not a participant regardless of `station_number`. Do
        not simplify this to `if not name` -- that silently drops every
        anonymous finisher again, which is the exact bug this predicate
        exists to fix.
        """
        if not isinstance(row, dict):
            return False
        name = row.get("athlete_name")
        if isinstance(name, str) and name.strip():
            return True
        if name is None and row.get("station_number") is not None:
            return True
        return False

    @staticmethod
    def _participant_rows(leaderboard: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for node_id, row in leaderboard.items():
            if not RaceResultsQuery._is_participant(row):
                continue
            enriched = dict(row)
            enriched.setdefault("node_id", node_id)
            rows.append(enriched)
        return rows

    @classmethod
    def _rank_and_tag(
        cls, rows: list[dict[str, Any]], race_type: str, result_id: str
    ) -> list[dict[str, Any]]:
        ordered = cls._order_by_race_type(rows, race_type)
        ranked = []
        for index, row in enumerate(ordered, start=1):
            tagged = dict(row)
            tagged["rank"] = index
            tagged["token"] = _make_token(result_id, row.get("node_id"))
            ranked.append(tagged)
        return ranked

    @classmethod
    def _rank_and_tag_mixed(
        cls,
        rows: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        result_id: str,
    ) -> list[dict[str, Any]]:
        """Rank a mixed race's rows one equipment group at a time -- each
        group is ordered (and ranked from 1) with its OWN race_type, never
        the whole race's. Output is group 0's ranked rows, then group 1's,
        and so on; rows whose group_index is None or out of range (no group
        claimed that equipment) are appended last with rank None. Every row
        keeps a token regardless of group."""
        buckets: dict[int, list[dict[str, Any]]] = {
            index: [] for index in range(len(groups))
        }
        ungrouped: list[dict[str, Any]] = []
        for row in rows:
            group_index = row.get("group_index")
            if isinstance(group_index, int) and group_index in buckets:
                buckets[group_index].append(row)
            else:
                ungrouped.append(row)

        ranked: list[dict[str, Any]] = []
        for index, group in enumerate(groups):
            ordered = cls._order_by_race_type(buckets[index], group.get("race_type"))
            for rank, row in enumerate(ordered, start=1):
                tagged = dict(row)
                tagged["rank"] = rank
                tagged["token"] = _make_token(result_id, row.get("node_id"))
                ranked.append(tagged)
        for row in ungrouped:
            tagged = dict(row)
            tagged["rank"] = None
            tagged["token"] = _make_token(result_id, row.get("node_id"))
            ranked.append(tagged)
        return ranked

    @staticmethod
    def _category_label(race_type: Any, config: dict[str, Any]) -> Optional[str]:
        if race_type in _TARGET_RACE_TYPES:
            target = config.get("target_value")
            if not isinstance(target, (int, float)) or target <= 0:
                return None
            unit = "m" if race_type == "distance" else "cal"
            return f"{_format_number(target)} {unit}"
        if race_type in _TIME_BOXED_RACE_TYPES or race_type == "max_power":
            duration_sec = config.get("duration_sec")
            if not isinstance(duration_sec, (int, float)) or duration_sec <= 0:
                return None
            return f"{_format_number(duration_sec / 60)} min"
        return None

    @staticmethod
    def _record_value(race_type: Any, row: dict[str, Any]) -> Optional[float]:
        if race_type in _TARGET_RACE_TYPES:
            finished = row.get("finished_time_ms")
            return _as_number(finished) if finished is not None else None
        if race_type in _TIME_BOXED_RACE_TYPES:
            return _as_number(row.get("distance_m"))
        if race_type == "max_power":
            return _as_number(row.get("max_power_watts"))
        return None

    _STATION_PLACEHOLDER_PREFIX = "Station "

    @staticmethod
    def _is_genuine_name(row: dict[str, Any], stripped_name: Optional[str]) -> bool:
        """Whether `stripped_name` is a real, registered athlete/team name
        that should merge duplicate rows of the same person -- rather than
        a station-number placeholder (e.g. "Station 1") that two entirely
        different, never-registered participants across different heats can
        share verbatim.

        Prefers the robust signal set by RaceManager.update_telemetry,
        `is_registered_name`, when the stored row actually has it. Falls
        back to the previous string-prefix heuristic only for older stored
        rows saved before that field existed, so historical data doesn't
        regress.
        """
        if stripped_name is None:
            return False
        if "is_registered_name" in row and row["is_registered_name"] is not None:
            return bool(row["is_registered_name"])
        return not stripped_name.startswith(
            RaceResultsQuery._STATION_PLACEHOLDER_PREFIX
        )

    @staticmethod
    def _top_three(rows: list[dict[str, Any]], race_type: Any) -> list[dict[str, Any]]:
        # distance/calories records rank the fastest finish (ascending);
        # everything else ranks the biggest number (descending).
        ascending = race_type in _TARGET_RACE_TYPES
        ordered = sorted(rows, key=lambda r: r["value"], reverse=not ascending)

        # A named athlete who raced this category more than once must only
        # occupy one record slot -- keep their best row (the first one we
        # meet in `ordered`, since it's already sorted best-first) and drop
        # the rest. Anonymous rows (athlete_name is None) and station-
        # placeholder rows (see `_is_genuine_name`) are never merged with
        # each other -- only a shared, stripped, non-empty, GENUINE name
        # (within the same division) merges.
        deduped = []
        seen_names: set[tuple[str, Any]] = set()
        for row in ordered:
            name = row.get("athlete_name")
            stripped = name.strip() if isinstance(name, str) else None
            if RaceResultsQuery._is_genuine_name(row, stripped):
                key = (stripped, row.get("division"))
                if key in seen_names:
                    continue
                seen_names.add(key)
            deduped.append(row)

        return [
            {
                "athlete_name": r["athlete_name"],
                "team_name": r["team_name"],
                "division": r.get("division"),
                "value": r["value"],
                "end_time_epoch_ms": r["end_time_epoch_ms"],
            }
            for r in deduped[:3]
        ]

    @staticmethod
    def _order_by_race_type(
        rows: list[dict[str, Any]], race_type: str
    ) -> list[dict[str, Any]]:
        if race_type in _TARGET_RACE_TYPES:
            metric_field = "distance_m" if race_type == "distance" else "calories"
            finishers = [r for r in rows if r.get("finished_time_ms") is not None]
            non_finishers = [r for r in rows if r.get("finished_time_ms") is None]
            finishers.sort(key=lambda r: _as_number(r.get("finished_time_ms")))
            non_finishers.sort(
                key=lambda r: _as_number(r.get(metric_field)), reverse=True
            )
            return finishers + non_finishers
        if race_type in _TIME_BOXED_RACE_TYPES:
            return sorted(
                rows, key=lambda r: _as_number(r.get("distance_m")), reverse=True
            )
        if race_type == "max_power":
            return sorted(
                rows, key=lambda r: _as_number(r.get("max_power_watts")), reverse=True
            )
        return list(rows)

    # -- get_standings() internals --------------------------------------

    @staticmethod
    def _record_config(record: Any) -> Optional[dict[str, Any]]:
        """The record's config dict, or None if the record isn't a
        well-formed stored race (mirrors the guard in `get_records`)."""
        if not isinstance(record, dict):
            return None
        snapshot = record.get("snapshot")
        if not isinstance(snapshot, dict):
            return None
        config = snapshot.get("config")
        return config if isinstance(config, dict) else {}

    def _records_since(self, event_start_epoch_ms: Optional[float]) -> list[Any]:
        """Every stored record (newest-first, per `_load_records`), or --
        when `event_start_epoch_ms` is given -- only those whose snapshot
        start_time_epoch_ms is a number >= the boundary. A record with a
        missing/non-numeric start time is dropped once a boundary is set:
        it can't be shown to be at/after the boundary, so it's excluded
        rather than assumed current."""
        records = self._load_records()
        if event_start_epoch_ms is None:
            return records
        filtered = []
        for record in records:
            config_snapshot = (
                record.get("snapshot") if isinstance(record, dict) else None
            )
            if not isinstance(config_snapshot, dict):
                continue
            start = config_snapshot.get("start_time_epoch_ms")
            if isinstance(start, (int, float)) and start >= event_start_epoch_ms:
                filtered.append(record)
        return filtered

    def _latest_standings_scope(
        self, records: list[Any]
    ) -> Optional[tuple[Any, str, Any]]:
        """(race_type, label, relay_legs) of the MOST RECENTLY stored race
        in `records` -- and only that race. Deliberately does NOT skip past
        it to an older category: if the newest race is "mixed" (or
        otherwise not a well-formed target/time-boxed race -- see
        `_category_label`), there is no scope and standings are empty, so
        the dashboard falls back to the existing mixed per-group slides
        right after a mixed race instead of silently showing a stale
        ranking for whatever category preceded it. Division is
        intentionally left out of the scope so per-division heats of the
        same event share one scope."""
        if not records:
            return None
        config = self._record_config(records[0])
        if config is None:
            return None
        race_type = config.get("race_type")
        if race_type == "mixed":
            return None
        label = self._category_label(race_type, config)
        if label is None:
            return None
        return race_type, label, config.get("relay_legs")

    def _matches_scope(
        self,
        config: dict[str, Any],
        race_type: Any,
        label: str,
        relay_legs: Any,
    ) -> bool:
        if config.get("race_type") != race_type:
            return False
        if config.get("relay_legs") != relay_legs:
            return False
        return self._category_label(race_type, config) == label

    @staticmethod
    def _standings_finished_value(
        race_type: Any, row: dict[str, Any]
    ) -> tuple[bool, float]:
        """(finished, value) for one standings row. Target race types
        (distance/calories) can DNF, so `finished` reflects whether the
        row actually crossed the line, with `value` falling back to
        progress for a DNF row. Every other race type always "finishes"
        once its duration elapses -- there's no DNF concept for time/watts/
        max_power -- so `value` is simply the ranking metric.
        """
        if race_type in _TARGET_RACE_TYPES:
            finished_time_ms = row.get("finished_time_ms")
            if finished_time_ms is not None:
                return True, _as_number(finished_time_ms)
            progress_field = "distance_m" if race_type == "distance" else "calories"
            return False, _as_number(row.get(progress_field))
        if race_type == "max_power":
            return True, _as_number(row.get("max_power_watts"))
        return True, _as_number(row.get("distance_m"))

    @staticmethod
    def _dedupe_best_row(
        tagged_rows: list[tuple[dict[str, Any], bool, float]],
    ) -> list[tuple[dict[str, Any], bool, float]]:
        """Keep only a named athlete's best row across every contributing
        race (same rule as `_top_three`: a stripped, non-empty, GENUINE
        name -- see `_is_genuine_name` -- within the same division merges;
        anonymous and station-placeholder rows never merge, since a
        station-fallback name like "Station 1" is shared by whichever
        different, never-registered participant used that station in each
        heat). The input must already be ordered best-first (see
        `_order_by_race_type`, which puts every finisher ahead of every
        non-finisher for target race types) so "first occurrence wins" is
        enough to also guarantee a finisher always beats that same
        athlete's DNF row.
        """
        deduped = []
        seen_names: set[tuple[str, Any]] = set()
        for row, finished, value in tagged_rows:
            name = row.get("athlete_name")
            stripped = name.strip() if isinstance(name, str) else None
            if RaceResultsQuery._is_genuine_name(row, stripped):
                key = (stripped, row.get("division"))
                if key in seen_names:
                    continue
                seen_names.add(key)
            deduped.append((row, finished, value))
        return deduped

    @staticmethod
    def _division_sort_key(division: Any) -> tuple[int, str]:
        if division is None:
            return (0, "")
        if division == "men":
            return (1, "")
        if division == "women":
            return (2, "")
        return (3, str(division))
