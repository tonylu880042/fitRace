import logging
from collections import deque
from typing import Any, Awaitable, Callable, Iterable, Optional
from urllib.parse import urlencode

from hub_server.usecases.signup_token import make_signup_token, verify_signup_token

logger = logging.getLogger("hub_server.cloud_signup")

MAX_CLOUD_NAME_LENGTH = 20
_SEEN_IDS_LIMIT = 1000


def choose_signup_station(
    assigned: Iterable[int], registered: Iterable[int]
) -> Optional[int]:
    """Station a cloud QR should point at: the first assigned station still
    waiting for an athlete, else the lowest assigned one."""
    ordered = sorted(assigned)
    if not ordered:
        return None
    taken = set(registered)
    return next((sn for sn in ordered if sn not in taken), ordered[0])


def build_cloud_signup_url(base_url: str, venue: str, station: int, token: str) -> str:
    query = urlencode({"v": venue, "s": station, "t": token})
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{query}"


class CloudSignupProcessor:
    """Turns claims pulled from the cloud into station registrations.

    All I/O is injected: fetch_claims()/register() are awaitables, the rest
    plain callables, so the decision logic is testable with a fake clock.
    """

    def __init__(
        self,
        *,
        secret: str,
        venue: str,
        fetch_claims: Callable[[], Awaitable[list]],
        register: Callable[[int, str, Optional[str]], Awaitable[None]],
        assigned_stations: Callable[[], Iterable[int]],
        station_open: Callable[[int], bool],
        now_s: Callable[[], float],
        on_claims_pulled: Callable[[], None] = lambda: None,
    ):
        self._secret = secret
        self._venue = venue
        self._fetch_claims = fetch_claims
        self._register = register
        self._assigned_stations = assigned_stations
        self._station_open = station_open
        self._now_s = now_s
        self._on_claims_pulled = on_claims_pulled
        self._used_tokens: dict[str, None] = {}
        self._queue: deque[dict[str, Any]] = deque()
        self._seen_ids: dict[str, None] = {}

    @property
    def queue_length(self) -> int:
        return len(self._queue)

    async def tick(self) -> int:
        """One pull + drain pass. Returns how many athletes were registered."""
        claims = await self._fetch_claims()
        if claims:
            # A claim was used (valid or not): the QR on screen is spent.
            self._on_claims_pulled()
        for claim in claims:
            self._ingest(claim)
        return await self._drain()

    def _ingest(self, claim: Any) -> None:
        # Never log the claim body: it carries the participant's photo.
        if not isinstance(claim, dict):
            logger.warning("Dropping malformed cloud claim (not an object)")
            return
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id:
            logger.warning("Dropping cloud claim without an id")
            return
        if claim_id in self._seen_ids:
            return
        reason = self._reject_reason(claim)
        if reason:
            logger.warning("Dropping cloud claim %s: %s", claim_id, reason)
            return
        self._remember(self._seen_ids, claim_id)
        self._remember(self._used_tokens, claim["token"])
        self._queue.append(
            {
                "id": claim_id,
                "station": claim["station"],
                "name": claim["name"].strip(),
                "avatar_base64": claim.get("avatar_base64") or None,
            }
        )

    @staticmethod
    def _remember(seen: dict, key: str) -> None:
        seen[key] = None
        while len(seen) > _SEEN_IDS_LIMIT:
            seen.pop(next(iter(seen)))

    def _expiry_reference_s(self, claim: dict) -> float:
        """The instant to judge token expiry at: when the cloud accepted the
        claim (received_at), so the ~1.5 s pull delay cannot expire a claim
        the visitor was already told succeeded. Falls back to hub time if
        received_at is not an int or claims to be from the future."""
        now = self._now_s()
        received = claim.get("received_at")
        if isinstance(received, int) and not isinstance(received, bool):
            if received <= now:
                return received
        return now

    def _reject_reason(self, claim: dict) -> Optional[str]:
        station = claim.get("station")
        name = claim.get("name")
        if claim.get("venue") != self._venue:
            return "venue mismatch"
        if not isinstance(station, int) or isinstance(station, bool):
            return "invalid station"
        if station not in set(self._assigned_stations()):
            return "station not assigned"
        if not verify_signup_token(
            self._secret,
            self._venue,
            station,
            claim.get("token"),
            self._expiry_reference_s(claim),
        ):
            return "invalid or expired token"
        if claim["token"] in self._used_tokens:
            return "token already used"
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= (
            MAX_CLOUD_NAME_LENGTH
        ):
            return "invalid name"
        avatar = claim.get("avatar_base64")
        if avatar is not None and not isinstance(avatar, str):
            return "invalid avatar"
        return None

    async def _drain(self) -> int:
        registered = 0
        waiting: deque[dict[str, Any]] = deque()
        for item in self._queue:
            if not self._station_open(item["station"]):
                waiting.append(item)
                continue
            try:
                await self._register(
                    item["station"], item["name"], item["avatar_base64"]
                )
                registered += 1
            except Exception as exc:
                logger.warning(
                    "Cloud claim %s could not be registered: %s", item["id"], exc
                )
        self._queue = waiting
        return registered


ONLINE_WINDOW_SEC = 30


def build_signup_fields(
    *,
    cloud_base_url: Optional[str],
    secret: Optional[str],
    venue: Optional[str],
    last_success_epoch_s: Optional[float],
    now_s: float,
    token_exp_epoch_s: int,
    token_nonce: str,
    issue_tokens: bool,
    assigned: Iterable[int],
    registered: Iterable[int],
    lan_url: Optional[str],
    queue_length: int,
) -> dict[str, Any]:
    """The three race-state fields the projector's sign-up QR is driven by.

    Cloud URL only while the cloud was reachable within ONLINE_WINDOW_SEC,
    tokens are being issued (challenge mode on) and there is a station to
    point at; otherwise the LAN sign-up page, so a venue
    that loses internet keeps working. cloud_base_url None = feature off.
    """
    online = (
        cloud_base_url is not None
        and last_success_epoch_s is not None
        and now_s - last_success_epoch_s <= ONLINE_WINDOW_SEC
    )
    station = choose_signup_station(assigned, registered)
    if online and issue_tokens and station is not None and secret and venue:
        token = make_signup_token(
            secret, venue, station, token_exp_epoch_s, token_nonce
        )
        url: Optional[str] = build_cloud_signup_url(
            cloud_base_url, venue, station, token
        )
    else:
        url = lan_url
    return {
        "signup_url": url,
        "cloud_signup_online": bool(online),
        "cloud_signup_queue_length": queue_length,
    }
