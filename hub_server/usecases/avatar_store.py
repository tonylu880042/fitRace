import re
import uuid
from pathlib import Path
from typing import Optional

_AVATAR_ID_RE = re.compile(r"[0-9a-f]{32}")


def is_valid_avatar_id(avatar_id: object) -> bool:
    return isinstance(avatar_id, str) and _AVATAR_ID_RE.fullmatch(avatar_id) is not None


class AvatarStore:
    """One WebP file per registration, named by a random 32-hex id.

    The id is the only thing ever turned into a filesystem path, and it is
    validated against a strict pattern first, so a request can never reach
    outside the store directory.
    """

    def __init__(self, directory: str | Path):
        self._dir = Path(directory)

    def save(self, webp_bytes: bytes) -> str:
        avatar_id = uuid.uuid4().hex
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{avatar_id}.webp").write_bytes(webp_bytes)
        return avatar_id

    def path_for(self, avatar_id: object) -> Optional[Path]:
        if not is_valid_avatar_id(avatar_id):
            return None
        path = self._dir / f"{avatar_id}.webp"
        return path if path.is_file() else None
