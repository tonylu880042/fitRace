import hashlib
import hmac


def _signature(secret: str, venue: str, station: int, exp_epoch_s: int) -> str:
    message = f"{venue}|{station}|{exp_epoch_s}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:16]


def make_signup_token(secret: str, venue: str, station: int, exp_epoch_s: int) -> str:
    """ "<exp>.<first 16 hex of HMAC-SHA256>" -- bound to venue and station,
    deliberately NOT single-use (several people may scan one QR)."""
    return f"{exp_epoch_s}.{_signature(secret, venue, station, exp_epoch_s)}"


def verify_signup_token(
    secret: str, venue: str, station: int, token: object, now_epoch_s: float
) -> bool:
    if not isinstance(token, str):
        return False
    exp_text, dot, signature = token.partition(".")
    if not dot or not exp_text.isdigit():
        return False
    exp_epoch_s = int(exp_text)
    expected = _signature(secret, venue, station, exp_epoch_s)
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return False
    return now_epoch_s <= exp_epoch_s
