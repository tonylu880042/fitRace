"""C2: the projector's overall standings asks the hub for only the top 10
rows per section (GET /api/results/standings?limit=10) and shows each row's
registration photo, with a letter badge when there is none.

Runs the real refreshRecordWallData() and renderStandingsRows() from
index.html under node.
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
    marker = f"function {name}("
    start = source.index(marker)
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


def _node(js: str) -> dict:
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


_STUBS = """
const t = (key) => key;
function escapeHtml(v) { return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function metricNumber(v, fb = 0) { const n = Number(v); return Number.isFinite(n) ? n : fb; }
function formatRecordWallEntryValue() { return "VALUE"; }
"""


def _render_rows(rows_js: str) -> str:
    script = _script()
    js = f"""
{_STUBS}
{_function(script, "renderStandingsRows")}
console.log(JSON.stringify({{ html: renderStandingsRows({rows_js}, "time") }}));
"""
    return _node(js)["html"]


def test_row_with_a_photo_shows_it_in_an_img():
    html = _render_rows(
        '[{rank: 1, athlete_name: "Amy", avatar_url: "/api/avatars/'
        + "a" * 32
        + '.webp"}]'
    )
    assert f'<img class="standings-avatar" src="/api/avatars/{"a" * 32}.webp"' in html
    assert "standings-avatar-fallback" not in html


def test_row_without_a_photo_gets_an_initial_badge():
    html = _render_rows('[{rank: 2, athlete_name: "bob", avatar_url: null}]')
    assert "<img" not in html
    assert 'class="standings-avatar standings-avatar-fallback">B<' in html


def test_every_row_gets_an_avatar_slot():
    html = _render_rows(
        '[{rank: 1, athlete_name: "A", avatar_url: "/x.webp"},'
        ' {rank: 2, athlete_name: "B"}, {rank: 3, athlete_name: null, station_number: 4}]'
    )
    assert html.count('class="standings-avatar') == 3


def test_photo_url_is_html_escaped():
    html = _render_rows(
        '[{rank: 1, athlete_name: "A", avatar_url: "x\\"onerror=\\"1"}]'
    )
    assert 'onerror="1"' not in html


def test_refresh_requests_only_the_top_10_standings_rows():
    script = _script()
    js = f"""
const urls = [];
let recordWallActive = true;
let recordWallStandingsMode = false;
let recordWallSlides = [];
let recordWallIndex = 0;
global.fetch = async (url) => {{ urls.push(url); return {{ ok: false }}; }};
function buildOverallStandingsView() {{ return null; }}
function buildRecordWallSlides() {{ return []; }}
function updateRecordWallRotateTimer() {{}}
function renderRecordWallSlide() {{}}
function scrollRecordWallIntoView() {{}}
async {_function(script, "refreshRecordWallData")}
(async () => {{
  await refreshRecordWallData();
  console.log(JSON.stringify({{ urls }}));
}})();
"""
    urls = _node(js)["urls"]
    standings = [u for u in urls if "/api/results/standings" in u]
    assert len(standings) == 1
    assert re.search(r"[?&]limit=10(&|$)", standings[0])
