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
    first_signup_epoch_ms: Optional[int],
    start_wait_ms: int,
) -> Optional[ChallengeAction]:
    """What the challenge-mode scheduler should do right now (pure).

    "start":     READY, no countdown running, at least one athlete signed up
                 on an assigned station, and either every assigned station
                 has an athlete or the start window (first sign-up plus
                 start_wait_ms) has ended. With one station that is simply
                 "the sign-up arrived".
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
        registered = assigned & set(registered_stations)
        if not registered:
            return None
        if registered == assigned:
            return "start"
        window_over = (
            first_signup_epoch_ms is not None
            and now_epoch_ms >= first_signup_epoch_ms + start_wait_ms
        )
        return "start" if window_over else None
    if state == RaceState.STOPPED and pending_signups > 0:
        # The athlete who just finished gets to see their result first.
        shown_long_enough = (
            end_time_epoch_ms is None
            or now_epoch_ms >= end_time_epoch_ms + min_result_ms
        )
        return "reset" if shown_long_enough else None
    return None


def challenge_shows_standings(
    *,
    enabled: bool,
    state: RaceState,
    end_time_epoch_ms: Optional[int],
    now_epoch_ms: int,
    min_result_ms: int,
    registered_stations: Iterable[int],
) -> bool:
    """Should the projector show the top-10 standings right now?

    Between runs of a challenge: once the result screen has been up for
    min_result_ms (STOPPED), and while waiting for the next athlete (IDLE or
    READY with nobody signed up). Never during a run or once someone is
    registered for the next one.
    """
    if not enabled:
        return False
    if state == RaceState.STOPPED:
        return (
            end_time_epoch_ms is None
            or now_epoch_ms >= end_time_epoch_ms + min_result_ms
        )
    if state in (RaceState.IDLE, RaceState.READY):
        return not list(registered_stations)
    return False


def challenge_start_at(
    *,
    enabled: bool,
    state: RaceState,
    assigned_stations: Iterable[int],
    registered_stations: Iterable[int],
    first_signup_epoch_ms: Optional[int],
    start_wait_ms: int,
) -> Optional[int]:
    """When the pending run will start on its own, or None unless the hub is
    actually waiting for more stations (some, but not all, signed up)."""
    if not enabled or state != RaceState.READY or first_signup_epoch_ms is None:
        return None
    assigned = set(assigned_stations)
    registered = assigned & set(registered_stations)
    if not registered or registered == assigned:
        return None
    return first_signup_epoch_ms + start_wait_ms
