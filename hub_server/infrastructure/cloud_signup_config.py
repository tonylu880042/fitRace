from dataclasses import dataclass
from typing import Mapping, Optional

_REQUIRED = (
    "FITRACE_CLOUD_SIGNUP_URL",
    "FITRACE_CLOUD_SIGNUP_SECRET",
    "FITRACE_VENUE_ID",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)


@dataclass(frozen=True)
class CloudSignupConfig:
    base_url: str
    secret: str
    venue: str
    upstash_url: str
    upstash_token: str


def load_cloud_signup_config(env: Mapping[str, str]) -> Optional[CloudSignupConfig]:
    """All five variables must be set (non-blank) or the whole feature stays
    off and the hub behaves exactly as it did before cloud sign-up existed."""
    values = [(env.get(name) or "").strip() for name in _REQUIRED]
    if not all(values):
        return None
    return CloudSignupConfig(*values)
