"""Regression tests for locally-generated dashboard QR codes.

The dashboard (hub_server/static/index.html) used to build QR code <img> src
URLs by calling the public internet service api.qrserver.com. FitRaceStudio
is local-network-only during live races, so at a venue with no WAN those
images failed to load (blank/broken image). The fix generates the QR code
on the hub itself via a new GET /api/qr.svg endpoint (segno, pure Python, no
network dependency) and points the dashboard's qrCodeUrl() at it instead.

These tests verify:
1. GET /api/qr.svg returns a real SVG QR code for a given `data` value.
2. Two different `data` values actually produce different SVG bodies (proves
   the endpoint really encodes the payload, rather than serving a static
   placeholder image that happens to satisfy a shallow assertion).
3. Missing/empty `data` -> 400.
4. Data too long for a QR symbol -> 400, not a 500 (segno's
   DataOverflowError must be caught, not left to bubble up).
5. index.html's script no longer references api.qrserver.com anywhere, and
   does reference the new /api/qr.svg endpoint -- with JS comments stripped
   first, so a comment containing the same substrings can't satisfy the
   assertion while the real implementation is missing/reverted.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app

client = TestClient(app)

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# Comment-stripping helpers (CLAUDE.md: a comment must not satisfy a source
# assertion). Mirrors the technique used in
# tests/unit/hub/test_system_admin_clear_all_stations.py.
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _index_script_no_comments() -> str:
    source = _read_index()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


# ---------------------------------------------------------------------------
# 1 & 2. The endpoint returns a real, payload-dependent SVG QR code
# ---------------------------------------------------------------------------


def test_qr_svg_endpoint_returns_svg_image():
    res = client.get(
        "/api/qr.svg", params={"data": "http://192.168.0.130:8000/gameAdmin"}
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    body = res.text
    assert body.startswith("<?xml") or "<svg" in body


def test_qr_svg_endpoint_sets_no_store_cache_header():
    res = client.get(
        "/api/qr.svg", params={"data": "http://192.168.0.130:8000/gameAdmin"}
    )
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-store"


def test_qr_svg_endpoint_encodes_the_payload():
    res_a = client.get(
        "/api/qr.svg", params={"data": "http://192.168.0.130:8000/gameAdmin"}
    )
    res_b = client.get(
        "/api/qr.svg", params={"data": "http://192.168.0.130:8000/systemAdmin"}
    )
    assert res_a.status_code == 200
    assert res_b.status_code == 200
    assert res_a.text != res_b.text


# ---------------------------------------------------------------------------
# 3. Missing/empty data -> 400
# ---------------------------------------------------------------------------


def test_qr_svg_missing_data_returns_400():
    res = client.get("/api/qr.svg")
    assert res.status_code == 400


def test_qr_svg_empty_data_returns_400():
    res = client.get("/api/qr.svg", params={"data": ""})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# 4. Data too long for a QR symbol -> 400, not 500
# ---------------------------------------------------------------------------


def test_qr_svg_data_too_long_returns_400_not_500():
    res = client.get("/api/qr.svg", params={"data": "x" * 5000})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# 5. index.html points at the local endpoint, never the external service
# ---------------------------------------------------------------------------


def test_index_html_never_references_external_qr_service():
    script = _index_script_no_comments()
    assert "api.qrserver.com" not in script


def test_index_html_uses_local_qr_endpoint():
    script = _index_script_no_comments()
    assert "/api/qr.svg" in script
