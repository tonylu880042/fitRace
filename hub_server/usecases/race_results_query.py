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


class RaceResultsQuery:
    """Read-only query layer over the append-only race results jsonl store."""

    def __init__(self, store: RaceResultStore):
        self._store = store

    def list_races(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            summary
            for summary, _, _ in itertools.islice(self._iter_races(), limit)
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

    def get_athlete_result(self, token: str) -> Optional[dict[str, Any]]:
        for summary, ranked_rows, _ in self._iter_races():
            for row in ranked_rows:
                if row["token"] == token:
                    return {
                        "race": summary,
                        "athlete": row,
                        "total_athletes": len(ranked_rows),
                    }
        return None

    # -- internals -----------------------------------------------------

    def _iter_races(self):
        """Yield (summary, ranked_rows, team_leaderboard) newest-first,
        skipping any record that doesn't look like a well-formed result."""
        for record in self._load_records():
            summary = self._summarize(record)
            if summary is None:
                continue
            snapshot = record["snapshot"]
            leaderboard = snapshot.get("leaderboard")
            rows = self._named_rows(leaderboard if isinstance(leaderboard, dict) else {})
            ranked_rows = self._rank_and_tag(rows, summary["race_type"], summary["result_id"])
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
            1
            for row in leaderboard.values()
            if isinstance(row, dict) and row.get("athlete_name")
        )
        return {
            "result_id": result_id,
            "race_type": config.get("race_type"),
            "competition_mode": config.get("competition_mode"),
            "start_time_epoch_ms": snapshot.get("start_time_epoch_ms"),
            "end_time_epoch_ms": snapshot.get("end_time_epoch_ms"),
            "athlete_count": athlete_count,
        }

    @staticmethod
    def _named_rows(leaderboard: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for node_id, row in leaderboard.items():
            if not isinstance(row, dict) or not row.get("athlete_name"):
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

    @staticmethod
    def _order_by_race_type(
        rows: list[dict[str, Any]], race_type: str
    ) -> list[dict[str, Any]]:
        if race_type in _TARGET_RACE_TYPES:
            metric_field = "distance_m" if race_type == "distance" else "calories"
            finishers = [r for r in rows if r.get("finished_time_ms") is not None]
            non_finishers = [r for r in rows if r.get("finished_time_ms") is None]
            finishers.sort(key=lambda r: _as_number(r.get("finished_time_ms")))
            non_finishers.sort(key=lambda r: _as_number(r.get(metric_field)), reverse=True)
            return finishers + non_finishers
        if race_type in _TIME_BOXED_RACE_TYPES:
            return sorted(rows, key=lambda r: _as_number(r.get("distance_m")), reverse=True)
        if race_type == "max_power":
            return sorted(rows, key=lambda r: _as_number(r.get("max_power_watts")), reverse=True)
        return list(rows)
