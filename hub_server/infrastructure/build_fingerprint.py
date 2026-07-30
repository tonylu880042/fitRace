"""Content fingerprint for a served static asset.

The hub's semantic ``APP_VERSION`` (fitrace_common/version.py) is a
hand-maintained string that does not necessarily change on every deploy, so
it cannot by itself tell a browser tab running yesterday's JavaScript apart
from one running today's. Hashing the bytes of the served dashboard HTML
gives an identifier that changes whenever the asset content changes, with no
extra bookkeeping required at release time.
"""

import hashlib
from pathlib import Path
from typing import Union

UNKNOWN_FINGERPRINT = "unknown"


def compute_build_fingerprint(path: Union[str, Path]) -> str:
    """Return a short, stable hex digest of the file at ``path``.

    Never raises: if the file cannot be read (missing, permissions, etc.)
    this returns :data:`UNKNOWN_FINGERPRINT` instead of propagating the
    error, so a missing static asset never crashes the app.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return UNKNOWN_FINGERPRINT
    return hashlib.sha256(data).hexdigest()[:12]
