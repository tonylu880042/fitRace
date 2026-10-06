"""C3: the projector's sign-up QR is driven by the backend state's
signup_url (cloud URL when online, LAN page otherwise), never rebuilt from
the browser's own idea of the hub address once the state has spoken.

Runs the real setSignupQrTarget()/applySignupUrl() from index.html under
node and asserts on what ends up in the QR <img> elements.
"""

import json
import re
import subprocess
from pathlib import Path

INDEX = Path(__file__).resolve().parents[3] / "hub_server" / "static" / "index.html"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _script() -> str:
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    code = source[start : source.index("</script>", start)]
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", code))


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth, i, in_str = 0, source.index("{", start), None
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


def _run(steps_js: str) -> dict:
    script = _script()
    fns = "\n".join(
        _function(script, n) for n in ("setSignupQrTarget", "applySignupUrl")
    )
    js = f"""
let currentSignupQrUrl = "";
let signupUrlFromState = false;
const imgs = [{{ src: "" }}, {{ src: "" }}];
let srcWrites = 0;
imgs.forEach((img) => {{
  let value = "";
  Object.defineProperty(img, "src", {{ get: () => value, set: (v) => {{ srcWrites++; value = v; }} }});
}});
const document = {{ querySelectorAll: () => imgs }};
{fns}
{steps_js}
console.log(JSON.stringify({{ current: currentSignupQrUrl, srcs: imgs.map(i => i.src), srcWrites }}));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _qr(url: str) -> str:
    from urllib.parse import quote

    return "/api/qr.svg?data=" + quote(url, safe="-_.!~*'()")


CLOUD = "https://signup.example.app/?v=gym&s=1&t=1800000600.0123456789abcdef"
LAN = "http://192.168.1.5:8000/static/signup.html"


def test_state_signup_url_becomes_the_qr_on_every_qr_image():
    result = _run(f"applySignupUrl({{ signup_url: {json.dumps(CLOUD)} }});")
    assert result["srcs"] == [_qr(CLOUD), _qr(CLOUD)]
    assert result["current"] == _qr(CLOUD)


def test_unchanged_state_url_does_not_rewrite_the_images():
    """The QR is redrawn on every state broadcast; touching src each time
    would make the projector QR flicker/reload."""
    result = _run(
        f"applySignupUrl({{ signup_url: {json.dumps(CLOUD)} }});"
        f"applySignupUrl({{ signup_url: {json.dumps(CLOUD)} }});"
    )
    assert result["srcWrites"] == 2  # one per image, from the first call only


def test_changed_state_url_updates_the_images_cloud_to_lan_fallback():
    result = _run(
        f"applySignupUrl({{ signup_url: {json.dumps(CLOUD)} }});"
        f"applySignupUrl({{ signup_url: {json.dumps(LAN)} }});"
    )
    assert result["srcs"] == [_qr(LAN), _qr(LAN)]


def test_state_without_a_url_leaves_the_qr_alone():
    result = _run(
        f"applySignupUrl({{ signup_url: {json.dumps(CLOUD)} }});"
        "applySignupUrl({ signup_url: null });"
        "applySignupUrl({});"
        "applySignupUrl(null);"
    )
    assert result["current"] == _qr(CLOUD)


def test_browser_derived_fallback_never_overrides_the_state_url():
    result = _run(
        f"applySignupUrl({{ signup_url: {json.dumps(CLOUD)} }});"
        f"setSignupQrTarget({json.dumps(LAN)}, false);"
    )
    assert result["current"] == _qr(CLOUD)


def test_browser_derived_fallback_is_used_until_the_state_provides_a_url():
    result = _run(f"setSignupQrTarget({json.dumps(LAN)}, false);")
    assert result["srcs"] == [_qr(LAN), _qr(LAN)]


def test_update_ui_state_applies_the_signup_url_before_rendering():
    """Structural guard: the state handler must call applySignupUrl(data),
    and before the leaderboard render that can rebuild empty-state QR
    blocks from currentSignupQrUrl."""
    handler = _function(_script(), "updateUIState")
    assert "applySignupUrl(data)" in handler
    assert handler.index("applySignupUrl(data)") < handler.index("renderLeaderboard(")
