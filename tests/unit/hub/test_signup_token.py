import pytest

from hub_server.usecases.signup_token import make_signup_token, verify_signup_token

SECRET = "venue-secret"
NOW = 1_800_000_000


def test_token_has_expiry_dot_16_hex_shape():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600)
    exp, sig = token.split(".")
    assert exp == str(NOW + 600)
    assert len(sig) == 16 and all(c in "0123456789abcdef" for c in sig)


def test_valid_token_verifies():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600)
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW) is True


def test_token_is_not_single_use():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600)
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW) is True
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW + 1) is True


def test_expired_token_is_rejected():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600)
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW + 599) is True
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW + 601) is False


@pytest.mark.parametrize(
    "secret,venue,station",
    [("other", "gym-a", 1), (SECRET, "gym-b", 1), (SECRET, "gym-a", 2)],
)
def test_token_is_bound_to_secret_venue_and_station(secret, venue, station):
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600)
    assert verify_signup_token(secret, venue, station, token, NOW) is False


def test_tampered_expiry_is_rejected():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600)
    _, sig = token.split(".")
    forged = f"{NOW + 99999}.{sig}"
    assert verify_signup_token(SECRET, "gym-a", 1, forged, NOW) is False


def test_tampered_signature_is_rejected():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600)
    exp, sig = token.split(".")
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert verify_signup_token(SECRET, "gym-a", 1, f"{exp}.{flipped}", NOW) is False


@pytest.mark.parametrize(
    "bad", ["", "abc", "1.2.3", "x.y", f"{NOW + 600}.", None, 12345, f".{'a' * 16}"]
)
def test_malformed_tokens_are_rejected_not_raised(bad):
    assert verify_signup_token(SECRET, "gym-a", 1, bad, NOW) is False
