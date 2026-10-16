"""Persistent relay team library and the pure team->station assignment
validator. No FastAPI here: ``store`` is any object with ``load()/save()``
(the RaceSettingsStore contract)."""

import uuid
from typing import Any, Optional

MAX_NAME_LEN = 80
MAX_MEMBERS = 10
MAX_MEMBER_LEN = 80


class RelayTeamLibrary:
    def __init__(self, store: Any):
        self._store = store
        self._teams: list[dict] = self._load()

    def _load(self) -> list[dict]:
        data = self._store.load()
        raw = data.get("teams") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        teams = []
        for item in raw:
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("members"), list)
            ):
                teams.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "members": [str(m) for m in item["members"]],
                    }
                )
        return teams

    def _persist(self) -> None:
        self._store.save({"teams": self._teams})

    @staticmethod
    def _copy(team: dict) -> dict:
        return {
            "id": team["id"],
            "name": team["name"],
            "members": list(team["members"]),
        }

    def teams(self) -> list[dict]:
        return [self._copy(t) for t in self._teams]

    def _find(self, team_id: str) -> dict:
        for team in self._teams:
            if team["id"] == team_id:
                return team
        raise ValueError("team not found")

    @staticmethod
    def _clean(name: Any, members: Any) -> tuple[str, list[str]]:
        clean_name = str(name or "").strip()
        if not 1 <= len(clean_name) <= MAX_NAME_LEN:
            raise ValueError(f"team name must be 1-{MAX_NAME_LEN} characters")
        clean_members = [m for m in (str(x).strip() for x in (members or [])) if m]
        if not 1 <= len(clean_members) <= MAX_MEMBERS:
            raise ValueError(f"team needs 1-{MAX_MEMBERS} members")
        if any(len(m) > MAX_MEMBER_LEN for m in clean_members):
            raise ValueError(f"member name must be at most {MAX_MEMBER_LEN} characters")
        return clean_name, clean_members

    def _check_unique(self, name: str, own_id: Optional[str]) -> None:
        lowered = name.lower()
        for team in self._teams:
            if team["id"] != own_id and team["name"].lower() == lowered:
                raise ValueError("team name already exists")

    def add(self, name: str, members: list[str]) -> dict:
        clean_name, clean_members = self._clean(name, members)
        self._check_unique(clean_name, None)
        team = {"id": uuid.uuid4().hex, "name": clean_name, "members": clean_members}
        self._teams.append(team)
        self._persist()
        return self._copy(team)

    def update(self, team_id: str, name: str, members: list[str]) -> dict:
        team = self._find(team_id)
        clean_name, clean_members = self._clean(name, members)
        self._check_unique(clean_name, team_id)
        team["name"] = clean_name
        team["members"] = clean_members
        self._persist()
        return self._copy(team)

    def delete(self, team_id: str) -> None:
        team = self._find(team_id)
        self._teams.remove(team)
        self._persist()


def plan_assignments(
    teams: list[dict],
    assignments: dict[int, Optional[str]],
    station_numbers: list[int],
    relay_legs: int,
) -> list[tuple[int, dict]]:
    """Validate a station->team_id mapping and return ``[(station, team)]``
    sorted by station. Everything is checked before anything is returned, so
    a failure never yields a partial plan."""
    by_id = {t["id"]: t for t in teams}
    plan: list[tuple[int, dict]] = []
    used: set[str] = set()
    for station in sorted(assignments):
        team_id = assignments[station]
        if team_id is None:
            continue
        if station not in station_numbers:
            raise ValueError(f"station {station} has no node assigned")
        team = by_id.get(team_id)
        if team is None:
            raise ValueError("team not found")
        if team_id in used:
            raise ValueError(
                f"team {team['name']} is assigned to more than one station"
            )
        used.add(team_id)
        if len(team["members"]) != relay_legs:
            raise ValueError(
                f"team {team['name']} has {len(team['members'])} members, "
                f"but this race needs {relay_legs}"
            )
        plan.append((station, team))
    return plan
