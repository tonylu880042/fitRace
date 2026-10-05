import pytest

from hub_server.usecases.signup_token import (
    SignupTokenIssuer,
    make_signup_token,
    verify_signup_token,
)

SECRET = "venue-secret"
NOW = 1_800_000_000
NONCE = "0a1b2c3d"

# Cross-language contract: cloud_signup/lib/token.test.js asserts this exact
# vector, so the hub and the Vercel function can never drift apart.
VECTOR = ("s3cret", "gym-a", 1, 1_800_000_300, "0a1b2c3d")
VECTOR_TOKEN = "1800000300.0a1b2c3d.f0f376ffab5d412a"


def test_cross_language_vector():
    assert make_signup_token(*VECTOR) == VECTOR_TOKEN
    assert verify_signup_token("s3cret", "gym-a", 1, VECTOR_TOKEN, 1_800_000_000)


def test_token_has_expiry_nonce_signature_shape():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    exp, nonce, sig = token.split(".")
    assert exp == str(NOW + 600)
    assert nonce == NONCE
    assert len(sig) == 16 and all(c in "0123456789abcdef" for c in sig)


def test_valid_token_verifies():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW) is True


def test_token_is_not_single_use():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW) is True
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW + 1) is True


def test_expired_token_is_rejected():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW + 599) is True
    assert verify_signup_token(SECRET, "gym-a", 1, token, NOW + 601) is False


@pytest.mark.parametrize(
    "secret,venue,station",
    [("other", "gym-a", 1), (SECRET, "gym-b", 1), (SECRET, "gym-a", 2)],
)
def test_token_is_bound_to_secret_venue_and_station(secret, venue, station):
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    assert verify_signup_token(secret, venue, station, token, NOW) is False


def test_tampered_expiry_is_rejected():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    _, nonce, sig = token.split(".")
    forged = f"{NOW + 99999}.{nonce}.{sig}"
    assert verify_signup_token(SECRET, "gym-a", 1, forged, NOW) is False


def test_tampered_signature_is_rejected():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    exp, nonce, sig = token.split(".")
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert (
        verify_signup_token(SECRET, "gym-a", 1, f"{exp}.{nonce}.{flipped}", NOW)
        is False
    )


def test_tampered_nonce_is_rejected():
    token = make_signup_token(SECRET, "gym-a", 1, NOW + 600, NONCE)
    exp, _, sig = token.split(".")
    assert (
        verify_signup_token(SECRET, "gym-a", 1, f"{exp}.deadbeef.{sig}", NOW) is False
    )


def test_token_without_nonce_is_rejected():
    assert (
        verify_signup_token(SECRET, "gym-a", 1, f"{NOW + 600}.0123456789abcdef", NOW)
        is False
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abc",
        "1.2",
        "1.2.3.4",
        "x.y.z",
        f"{NOW + 600}..",
        None,
        12345,
        f"..{'a' * 16}",
    ],
)
def test_malformed_tokens_are_rejected_not_raised(bad):
    assert verify_signup_token(SECRET, "gym-a", 1, bad, NOW) is False


def test_issuer_keeps_one_nonce_for_a_minute_then_rotates():
    nonces = iter(["n1", "n2", "n3"])
    issuer = SignupTokenIssuer(nonce_fn=lambda: next(nonces))
    assert issuer.current(NOW) == (NOW + 300, "n1")
    assert issuer.current(NOW + 59) == (NOW + 300, "n1")
    assert issuer.current(NOW + 60) == (NOW + 360, "n2")


def test_issuer_rotates_immediately_when_told_a_claim_was_used():
    nonces = iter(["n1", "n2"])
    issuer = SignupTokenIssuer(nonce_fn=lambda: next(nonces))
    assert issuer.current(NOW)[1] == "n1"
    issuer.rotate()
    assert issuer.current(NOW + 1) == (NOW + 301, "n2")
