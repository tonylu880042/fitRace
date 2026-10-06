import base64
import re
import uuid
from pathlib import Path
from typing import Optional

_AVATAR_ID_RE = re.compile(r"[0-9a-f]{32}")


def is_valid_avatar_id(avatar_id: object) -> bool:
    return isinstance(avatar_id, str) and _AVATAR_ID_RE.fullmatch(avatar_id) is not None


def sniff_image_type(data: bytes) -> Optional[str]:
    """Media type from magic bytes: WebP (RIFF....WEBP) or JPEG (FF D8 FF)."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def decode_avatar_image(avatar_base64: str, max_bytes: int) -> bytes:
    """Decode a WebP or JPEG avatar (data URL or bare base64). The data-URL
    header must agree with the bytes; PNG and everything else is rejected."""
    declared = None
    if "," in avatar_base64:
        header, body = avatar_base64.split(",", 1)
        declared = header.strip().lower()
        if declared not in ("data:image/webp;base64", "data:image/jpeg;base64"):
            raise ValueError("Avatar must be a WebP or JPEG data URL")
    else:
        body = avatar_base64
    data = base64.b64decode(body, validate=True)
    if not data:
        raise ValueError("Empty image data")
    if len(data) > max_bytes:
        raise ValueError("Avatar image is too large")
    actual = sniff_image_type(data)
    if actual is None:
        raise ValueError("Avatar must be a WebP or JPEG image")
    if declared is not None and declared != f"data:{actual};base64":
        raise ValueError("Avatar data URL does not match the image bytes (WebP/JPEG)")
    return data


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

    def list_ids(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return [
            path.stem
            for path in self._dir.glob("*.webp")
            if is_valid_avatar_id(path.stem)
        ]

    def delete(self, avatar_id: object) -> bool:
        path = self.path_for(avatar_id)
        if path is None:
            return False
        path.unlink()
        return True
