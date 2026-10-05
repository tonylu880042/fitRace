from typing import Iterable, Literal, Optional

from hub_server.domain.models import RaceState

ChallengeAction = Literal["start", "reset", "configure"]


def next_challenge_action(
    *,
    enabled: bool,
    state: RaceState,
    session_mode: str,
    assigned_stations: Iterable[int],
    registered_stations: Iterable[int],
    countdown_active: bool,
    pending_signups: int,
    now_epoch_ms: int,
    end_time_epoch_ms: Optional[int],
    min_result_ms: int,
) -> Optional[ChallengeAction]:
    """What the challenge-mode scheduler should do right now (pure).

    "start":     READY, every assigned station has an athlete, and no
                 countdown is already running.
    "reset":     STOPPED and at least one new sign-up is waiting -- caller
                 resets and re-applies the timed config, but not before the result
                 has been on screen for min_result_ms. With nobody waiting
                 the result screen simply stays up.
    "configure": IDLE (fresh boot, or a manual Reset) -- caller re-applies
                 the timed config.
    """
    if not enabled or session_mode != "race":
        return None
    if state == RaceState.IDLE:
        return "configure"
    if state == RaceState.READY:
        assigned = set(assigned_stations)
        if not assigned or countdown_active:
            return None
        return "start" if assigned <= set(registered_stations) else None
    if state == RaceState.STOPPED and pending_signups > 0:
        # The athlete who just finished gets to see their result first.
        shown_long_enough = (
            end_time_epoch_ms is None
            or now_epoch_ms >= end_time_epoch_ms + min_result_ms
        )
        return "reset" if shown_long_enough else None
    return None
