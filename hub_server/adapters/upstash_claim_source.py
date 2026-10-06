import asyncio
import json
import logging
import time
import urllib.request
from typing import Any, Callable, Optional

logger = logging.getLogger("hub_server.upstash_claim_source")

TIMEOUT_SEC = 5
POP_COUNT = "10"
STATIONS_TTL_SEC = 30


def _urllib_post_json(url: str, headers: dict, body: Any, timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


class UpstashClaimSource:
    """Pops queued sign-up claims from Upstash Redis over its REST API.

    Outbound HTTPS only. A network failure is never raised: it returns no
    claims and is recorded, so the hub keeps working offline.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        venue: str,
        post_json: Callable[[str, dict, Any, float], Any] = _urllib_post_json,
        now_s: Callable[[], float] = time.time,
    ):
        self._base_url = base_url
        self._token = token
        self._venue = venue
        self._key = f"fitrace:claims:{venue}"
        self._post_json = post_json
        self._now_s = now_s
        self.last_success_epoch_s: Optional[float] = None
        self.last_failure_epoch_s: Optional[float] = None

    async def fetch(self) -> list[dict]:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[dict]:
        try:
            response = self._post_json(
                self._base_url,
                {"Authorization": f"Bearer {self._token}"},
                ["LPOP", self._key, POP_COUNT],
                TIMEOUT_SEC,
            )
            if not isinstance(response, dict) or "error" in response:
                raise ValueError("Upstash returned an error response")
        except Exception as exc:
            self.last_failure_epoch_s = self._now_s()
            logger.warning("Cloud sign-up pull failed: %s", exc)
            return []
        self.last_success_epoch_s = self._now_s()
        raw = response.get("result") or []
        claims: list[dict] = []
        for entry in raw:
            try:
                parsed = json.loads(entry)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                claims.append(parsed)
        return claims

    async def publish_stations(self, snapshot: list) -> bool:
        return await asyncio.to_thread(self._publish_sync, snapshot)

    def _publish_sync(self, snapshot: list) -> bool:
        try:
            response = self._post_json(
                self._base_url,
                {"Authorization": f"Bearer {self._token}"},
                [
                    "SET",
                    f"fitrace:stations:{self._venue}",
                    json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False),
                    "EX",
                    str(STATIONS_TTL_SEC),
                ],
                TIMEOUT_SEC,
            )
            if not isinstance(response, dict) or "error" in response:
                raise ValueError("Upstash returned an error response")
        except Exception as exc:
            self.last_failure_epoch_s = self._now_s()
            logger.warning("Station snapshot publish failed: %s", exc)
            return False
        return True
