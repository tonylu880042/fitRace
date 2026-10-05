import hashlib
import hmac
import secrets
from typing import Callable, Optional

TOKEN_VALIDITY_SEC = 300
TOKEN_ROTATE_SEC = 60


def _signature(
    secret: str, venue: str, station: int, exp_epoch_s: int, nonce: str
) -> str:
    message = f"{venue}|{station}|{exp_epoch_s}|{nonce}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:16]


def make_signup_token(
    secret: str, venue: str, station: int, exp_epoch_s: int, nonce: str
) -> str:
    """ "<exp>.<nonce>.<first 16 hex of HMAC-SHA256>", bound to venue and
    station. Same algorithm as cloud_signup/lib/token.js (pinned by a shared
    test vector). One-time use is enforced by the cloud and, as defence in
    depth, by the processor -- not by this function."""
    sig = _signature(secret, venue, station, exp_epoch_s, nonce)
    return f"{exp_epoch_s}.{nonce}.{sig}"


def verify_signup_token(
    secret: str, venue: str, station: int, token: object, now_epoch_s: float
) -> bool:
    if not isinstance(token, str):
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    exp_text, nonce, signature = parts
    if not exp_text.isdigit() or not nonce:
        return False
    exp_epoch_s = int(exp_text)
    expected = _signature(secret, venue, station, exp_epoch_s, nonce)
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return False
    return now_epoch_s <= exp_epoch_s


class SignupTokenIssuer:
    """Hands out the (exp, nonce) the projector QR is built from. The nonce
    changes every TOKEN_ROTATE_SEC and immediately on rotate() (called when a
    claim is pulled), so a used QR is replaced by a fresh one."""

    def __init__(self, nonce_fn: Callable[[], str] = lambda: secrets.token_hex(4)):
        self._nonce_fn = nonce_fn
        self._issued_at: Optional[float] = None
        self._nonce = ""

    def rotate(self) -> None:
        self._issued_at = None

    def current(self, now_s: float) -> tuple[int, str]:
        if self._issued_at is None or now_s - self._issued_at >= TOKEN_ROTATE_SEC:
            self._issued_at = now_s
            self._nonce = self._nonce_fn()
        return int(self._issued_at) + TOKEN_VALIDITY_SEC, self._nonce
