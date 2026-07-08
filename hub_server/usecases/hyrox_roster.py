"""Hyrox roster: subjects (teams) and their member RFID tags.

Phase 6a. A subject is the racing unit -- an individual is a team of one; a
doubles/relay team is one subject with multiple member tags. RFID attribution
needs tag -> subject before an assignment exists (dynamic claim), so the roster
owns that mapping.
"""

from dataclasses import dataclass, field
from typing import Optional


# Expected member count per division; None means "no fixed size".
_DIVISION_SIZE = {"individual": 1, "doubles": 2, "relay": 4}


@dataclass
class RosterEntry:
    subject_id: str          # team_id; an individual is a team of one
    division: str            # individual | doubles | relay
    member_tags: list[str] = field(default_factory=list)
    member_names: list[str] = field(default_factory=list)


class HyroxRoster:
    def __init__(self):
        self._subjects: dict[str, RosterEntry] = {}
        self._tag_to_subject: dict[str, str] = {}

    def add_member(
        self,
        subject_id: str,
        division: str,
        member_tag: str,
        member_name: str,
    ) -> RosterEntry:
        """Add one member tag to a subject, creating the subject if new.

        Mirrors the per-person signup flow: each athlete registers their own
        tag under a shared subject id (team name, or the tag for individuals).
        """
        if member_tag in self._tag_to_subject and self._tag_to_subject[member_tag] != subject_id:
            raise ValueError(
                f"Tag {member_tag} is already registered to "
                f"{self._tag_to_subject[member_tag]}"
            )
        entry = self._subjects.get(subject_id)
        if entry is None:
            entry = RosterEntry(subject_id=subject_id, division=division)
            self._subjects[subject_id] = entry
        elif entry.division != division:
            raise ValueError(
                f"Subject {subject_id} already registered as {entry.division}, "
                f"not {division}"
            )
        if member_tag not in entry.member_tags:
            entry.member_tags.append(member_tag)
            entry.member_names.append(member_name)
            self._tag_to_subject[member_tag] = subject_id
        return entry

    def subject_for_tag(self, tag: str) -> Optional[str]:
        return self._tag_to_subject.get(tag)

    def get(self, subject_id: str) -> Optional[RosterEntry]:
        return self._subjects.get(subject_id)

    def all(self) -> list[RosterEntry]:
        return list(self._subjects.values())

    def overfilled(self) -> list[str]:
        """Subject ids whose member count exceeds their division size.

        A readiness warning, not a hard registration error -- a half-formed
        relay team is fine mid-signup, an over-formed one is a data mistake.
        """
        bad = []
        for entry in self._subjects.values():
            size = _DIVISION_SIZE.get(entry.division)
            if size is not None and len(entry.member_tags) > size:
                bad.append(entry.subject_id)
        return bad
