"""Avatar photos from browsers that cannot encode WebP (iOS Safari's canvas
returns a PNG data URL for toDataURL('image/webp')): the pages fall back to
JPEG, and the hub accepts WebP or JPEG -- never PNG -- judged by magic bytes.
"""

import base64
import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.usecases.avatar_store import decode_avatar_image, sniff_image_type

ROOT = Path(__file__).resolve().parents[3]
WEBP = b"RIFF\x04\x00\x00\x00WEBPVP8 xxxx"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIFxxxx"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 8


def _url(mime, data):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


# -- hub decoder -----------------------------------------------------------------


def test_sniff_by_magic_bytes():
    assert sniff_image_type(WEBP) == "image/webp"
    assert sniff_image_type(JPEG) == "image/jpeg"
    assert sniff_image_type(PNG) is None
    assert sniff_image_type(b"") is None
    assert sniff_image_type(b"RIFF") is None


def test_decoder_accepts_webp_and_jpeg_data_urls():
    assert decode_avatar_image(_url("image/webp", WEBP), 1000) == WEBP
    assert decode_avatar_image(_url("image/jpeg", JPEG), 1000) == JPEG


def test_decoder_still_accepts_a_bare_base64_body():
    assert decode_avatar_image(base64.b64encode(JPEG).decode(), 1000) == JPEG


@pytest.mark.parametrize(
    "mime,data",
    [
        ("image/png", PNG),  # PNG never
        ("image/webp", PNG),  # png bytes under a webp header
        ("image/jpeg", PNG),
        ("image/jpeg", WEBP),  # header/bytes mismatch
        ("image/webp", JPEG),
        ("image/gif", b"GIF89a" + b"x" * 10),
    ],
)
def test_decoder_rejects_png_and_header_byte_mismatches(mime, data):
    with pytest.raises(ValueError):
        decode_avatar_image(_url(mime, data), 1000)


def test_decoder_rejects_oversized_and_empty():
    with pytest.raises(ValueError, match="too large"):
        decode_avatar_image(_url("image/jpeg", JPEG + b"0" * 2000), 1000)
    with pytest.raises(ValueError):
        decode_avatar_image("data:image/jpeg;base64,", 1000)


def test_register_endpoint_accepts_jpeg_and_serves_it_as_jpeg():
    client = TestClient(hub_app.app)
    client.post("/api/race/reset")
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "jp-1"})
    res = client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": "Ios",
            "avatar_base64": _url("image/jpeg", JPEG),
        },
    )
    assert res.status_code == 200
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    rows = client.get("/api/race/state").json()["leaderboard"]
    url = next(r["avatar_url"] for r in rows.values() if r["station_number"] == 1)
    assert url.endswith(".webp")  # URL shape is unchanged
    served = client.get(url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content == JPEG
    client.post("/api/race/reset")


def test_register_endpoint_rejects_png():
    client = TestClient(hub_app.app)
    res = client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": "X",
            "avatar_base64": _url("image/png", PNG),
        },
    )
    assert res.status_code == 400


def test_avatar_endpoint_content_type_follows_the_stored_bytes():
    client = TestClient(hub_app.app)
    jpeg_id = hub_app.avatar_store.save(JPEG)
    webp_id = hub_app.avatar_store.save(WEBP)
    assert (
        client.get(f"/api/avatars/{jpeg_id}.webp").headers["content-type"]
        == "image/jpeg"
    )
    assert (
        client.get(f"/api/avatars/{webp_id}.webp").headers["content-type"]
        == "image/webp"
    )


# -- page encoders under node, with a Safari-like canvas -------------------------------

_LINE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _script(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    code = re.findall(r"<script>(.*?)</script>", src, re.S)[-1]
    return _LINE.sub("", _BLOCK.sub("", code))


def _function(source, name):
    start = source.index(f"function {name}(")
    a = source.rfind("async ", 0, start)
    if a != -1 and source[a:start] == "async ":
        start = a
    i = source.index("{", source.index(")", start))
    depth, in_str = 0, None
    while True:
        ch = source[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1


def _encode(path, supports_webp):
    script = _script(path)
    fns = "\n".join(
        _function(script, n)
        for n in (
            "estimateDataUrlBytes",
            "loadImage",
            "renderAvatarWebp",
            "convertAvatarSourceToWebp",
        )
    )
    js = f"""
const AVATAR_TARGET_SIZE = 96, AVATAR_MIN_SIZE = 64, AVATAR_MAX_OUTPUT_BYTES = 32 * 1024;
const AVATAR_QUALITY_STEPS = [0.72, 0.64, 0.56, 0.48];
const requested = [];
class Image {{
  set src(v) {{ this.naturalWidth = 200; this.naturalHeight = 100; setTimeout(() => this.onload(), 0); }}
}}
const document = {{ createElement: () => ({{
  getContext: () => ({{ drawImage() {{}} }}),
  toDataURL(mime, q) {{
    requested.push(mime);
    if (mime === "image/webp" && !{str(supports_webp).lower()}) return "data:image/png;base64,iVBORw0KGgo=";
    return `data:${{mime}};base64,AAAA`;
  }},
}}) }};
{fns}
convertAvatarSourceToWebp("x").then((d) => console.log(JSON.stringify({{ d, requested }})));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


PAGES = ["cloud_signup/public/index.html", "hub_server/static/signup.html"]


@pytest.mark.parametrize("page", PAGES)
def test_safari_like_canvas_ends_up_sending_a_jpeg_never_a_png(page):
    result = _encode(page, supports_webp=False)
    assert result["d"].startswith("data:image/jpeg;base64,")
    assert "png" not in result["d"]
    assert result["requested"][:2] == ["image/webp", "image/jpeg"]


@pytest.mark.parametrize("page", PAGES)
def test_webp_capable_browsers_still_send_webp(page):
    result = _encode(page, supports_webp=True)
    assert result["d"].startswith("data:image/webp;base64,")
    assert "image/jpeg" not in result["requested"]
