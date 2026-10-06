import re
from typing import Iterable, Optional

_URL_RE = re.compile(r"/api/avatars/([0-9a-f]{32})\.webp")


def avatar_id_from_url(url: object) -> Optional[str]:
    if not isinstance(url, str):
        return None
    match = _URL_RE.fullmatch(url)
    return match.group(1) if match else None


def partition_avatars(
    stored_ids: Iterable[str], keep: set[str]
) -> tuple[list[str], list[str]]:
    """(kept, doomed) from ONE pass over the stored ids, so a photo can never
    land in both lists or in neither -- the delete path trusts `doomed`."""
    kept: list[str] = []
    doomed: list[str] = []
    for avatar_id in stored_ids:
        (kept if avatar_id in keep else doomed).append(avatar_id)
    return kept, doomed
