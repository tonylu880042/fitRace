from hub_server.infrastructure.build_fingerprint import (
    UNKNOWN_FINGERPRINT,
    compute_build_fingerprint,
)


def test_fingerprint_is_stable_for_unchanged_content(tmp_path):
    asset = tmp_path / "index.html"
    asset.write_bytes(b"<html>hello</html>")

    first = compute_build_fingerprint(asset)
    second = compute_build_fingerprint(asset)

    assert first == second
    assert first  # non-empty


def test_fingerprint_changes_when_content_changes(tmp_path):
    asset = tmp_path / "index.html"

    asset.write_bytes(b"<html>version-one</html>")
    before = compute_build_fingerprint(asset)

    asset.write_bytes(b"<html>version-two</html>")
    after = compute_build_fingerprint(asset)

    assert before != after


def test_fingerprint_missing_file_returns_unknown_without_raising(tmp_path):
    missing = tmp_path / "does-not-exist.html"

    assert compute_build_fingerprint(missing) == UNKNOWN_FINGERPRINT
