import asyncio
import json
import logging
import time
import urllib.request
from typing import Any, Callable, Optional

logger = logging.getLogger("hub_server.upstash_claim_source")

TIMEOUT_SEC = 5
POP_COUNT = "10"


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
