"""Anonymous participation, frontend half (signup.html / index.html /
classAdmin.html / gameAdmin.html).

athlete_name is now optional end to end. The dangerous part flagged by
CLAUDE.md's testability guidance is a correct fallback that never reaches
the DOM: every assertion here executes the REAL, unmodified page function
under `node -e` (following tests/unit/hub/test_dashboard_class_board.py and
tests/unit/hub/test_class_admin_page.py) and checks the actual rendered
HTML string -- never a source-text grep for a helper's existence.

Where the fallback text itself is the thing under test ("Station 3"), the
real en-US locale file / real gameAdmin inline dictionary is loaded so the
assertion pins genuine translated copy, not a symbolic stub.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _script_body(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Mirrors tests/unit/hub/test_dashboard_class_board.py."""
    depth = 0
    i = open_idx
    in_str = None
    while i < len(source):
        char = source[i]
        if in_str:
            if char == "\\":
                i += 2
                continue
            if char == in_str:
                in_str = None
        elif char in ('"', "'", "`"):
            in_str = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing brace found")


def _extract_function(source: str, name: str, async_fn: bool = False) -> str:
    marker = f"{'async ' if async_fn else ''}function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _extract_braced(source: str, anchor: str) -> str:
    """Return the exact `{ ... }` block that immediately follows `anchor`,
    walking brace depth (mirrors tests/unit/hub/test_dashboard_class_board.py
    and tests/unit/hub/test_dashboard_stations_refetch_storm.py) so a
    template literal's own braces cannot throw off the match."""
    start = source.index(anchor) + len(anchor)
    brace_start = source.index("{", start)
    return source[brace_start : _matching_brace_end(source, brace_start) + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _escape_html_stub() -> str:
    return (
        "const escapeHtml = (value) => {\n"
        "  return String(value ?? '')\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;')\n"
        "    .replace(/\"/g, '&quot;')\n"
        "    .replace(/'/g, '&#039;');\n"
        "};\n"
    )


def _metric_number_stub() -> str:
    return (
        "const metricNumber = (value, fallback = 0) => {\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fallback;\n"
        "};\n"
    )


def _smooth_metric_number_stub() -> str:
    return (
        "const smoothMetricNumber = (key, targetValue) => Number(targetValue) || 0;\n"
    )


def _node_display_name_stub() -> str:
    return (
        "const nodeDisplayName = (node) => {\n"
        "  return node?.node_display_name || node?.display_name || node?.node_id || '--';\n"
        "};\n"
    )


def _intl_number_format_stub() -> str:
    return "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"


def _format_clock_stub() -> str:
    return (
        "function formatClock(ms) {\n"
        "  const safeMs = Math.max(0, metricNumber(ms));\n"
        "  const totalSeconds = Math.floor(safeMs / 1000);\n"
        "  const seconds = totalSeconds % 60;\n"
        "  const minutes = Math.floor(totalSeconds / 60);\n"
        "  return (minutes < 10 ? '0' + minutes : String(minutes)) + ':' +\n"
        "         (seconds < 10 ? '0' + seconds : String(seconds));\n"
        "}\n"
    )


def _real_t_from_messages(messages: dict) -> str:
    """A real (non-symbolic) t()/interpolate() pair, built from an actual
    locale dict, so assertions can pin genuine translated copy such as
    "Station 3" instead of a stub placeholder."""
    return (
        f"const __messages = {json.dumps(messages)};\n"
        "function interpolate(template, values) {\n"
        "  return String(template).replace(/\\{(\\w+)\\}/g, (_, key) => (values[key] ?? ''));\n"
        "}\n"
        "function t(key, values = {}) {\n"
        "  return interpolate(__messages[key] || key, values);\n"
        "}\n"
    )


def _en_us_messages() -> dict:
    with open(LOCALES_DIR / "en-US.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. index.html -- buildClassBoardHtml (the class board).
# ---------------------------------------------------------------------------


def _run_build_class_board_html(session_data_js: str, clock_js: str) -> str:
    source = _read("index.html")
    fn = _strip_js_comments(_extract_function(source, "buildClassBoardHtml"))
    script = (
        _real_t_from_messages(_en_us_messages())
        + _metric_number_stub()
        + _escape_html_stub()
        + _node_display_name_stub()
        + _intl_number_format_stub()
        + _format_clock_stub()
        + "const currentLocale = 'en-US';\n"
        + fn
        + "\n"
        + f"const sessionData = {session_data_js};\n"
        + f"const clock = {clock_js};\n"
        + "console.log(buildClassBoardHtml(sessionData, clock));"
    )
    return _run_node(script)


_ONE_SEGMENT_CLOCK = (
    '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, '
    '"totalRemainingMs": 300000, "finished": false}'
)


def test_class_board_shows_station_fallback_for_anonymous_athlete():
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": {
                "node1": {
                    "node_id": "node1",
                    "athlete_name": None,
                    "station_number": 3,
                    "power_watts": 100,
                    "instantaneous_speed_kph": 20,
                    "distance_m": 500,
                }
            },
        }
    )
    html = _run_build_class_board_html(session_data, _ONE_SEGMENT_CLOCK)
    # Appears twice: once as the station-number badge, once standing in for
    # the missing athlete name -- proving the fallback actually reached the
    # name slot, not merely that the digit shows up somewhere on the card.
    assert html.count("Station 3") == 2
    assert ">null<" not in html


def test_class_board_still_shows_real_name_when_present():
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": {
                "node1": {
                    "node_id": "node1",
                    "athlete_name": "Alice",
                    "station_number": 3,
                    "power_watts": 100,
                    "instantaneous_speed_kph": 20,
                    "distance_m": 500,
                }
            },
        }
    )
    html = _run_build_class_board_html(session_data, _ONE_SEGMENT_CLOCK)
    assert "Alice" in html
    # "Station 3" still legitimately appears once, as the station-number
    # badge -- the assertion is that it does NOT also appear a second time
    # standing in for the athlete's name.
    assert html.count("Station 3") == 1


# ---------------------------------------------------------------------------
# 2. index.html -- the classic race leaderboard row (renderLeaderboard's
# individual, non-team, non-alternate-display-mode branch).
# ---------------------------------------------------------------------------


def _classic_row_body() -> str:
    source = _read("index.html")
    script = _script_body(source)
    body = _extract_braced(script, "nodes.forEach((node, index) => ")
    return _strip_js_comments(body)


def _run_classic_leaderboard_row(nodes_js: str, race_type: str = "distance") -> str:
    body = _classic_row_body()
    script = (
        _real_t_from_messages(_en_us_messages())
        + _metric_number_stub()
        + _escape_html_stub()
        + _smooth_metric_number_stub()
        + _node_display_name_stub()
        + f"const raceType = {json.dumps(race_type)};\n"
        + "const leaderboardFinal = false;\n"
        + "const leaderboardRankByNode = new Map();\n"
        + "let html = '';\n"
        + f"const nodes = {nodes_js};\n"
        + f"nodes.forEach((node, index) => {body});\n"
        + "console.log(html);"
    )
    return _run_node(script)


def test_classic_leaderboard_row_shows_station_fallback_for_anonymous_athlete():
    html = _run_classic_leaderboard_row(
        json.dumps(
            [
                {
                    "node_id": "n1",
                    "athlete_name": None,
                    "station_number": 3,
                    "progress_percent": 10,
                    "distance_m": 50,
                    "instantaneous_speed_kph": 5,
                }
            ]
        )
    )
    # Appears twice: once as the station-pill badge, once standing in for
    # the missing athlete name.
    assert html.count("Station 3") == 2
    assert ">null<" not in html


def test_classic_leaderboard_row_still_shows_real_name_when_present():
    html = _run_classic_leaderboard_row(
        json.dumps(
            [
                {
                    "node_id": "n1",
                    "athlete_name": "Alice",
                    "station_number": 3,
                    "progress_percent": 10,
                    "distance_m": 50,
                    "instantaneous_speed_kph": 5,
                }
            ]
        )
    )
    assert "Alice" in html
    # "Station 3" still appears once, as the station-pill badge -- the
    # assertion is that it does not ALSO stand in for the name.
    assert html.count("Station 3") == 1


# ---------------------------------------------------------------------------
# 3. index.html -- showRegistrationMarquee must never render the literal
# word "null" and must fall back to the station.
# ---------------------------------------------------------------------------


def _run_show_registration_marquee(name_js: str, station: int) -> str:
    source = _read("index.html")
    fn = _strip_js_comments(_extract_function(source, "showRegistrationMarquee"))
    script = (
        _real_t_from_messages(_en_us_messages())
        + _escape_html_stub()
        + "const toastEl = { id: '', className: '', innerHTML: '', classList: { add() {}, remove() {} } };\n"
        + "const document = {\n"
        + "  getElementById: (id) => (id === 'marquee-toast' ? null : null),\n"
        + "  createElement: () => toastEl,\n"
        + "  body: { appendChild: () => {} },\n"
        + "};\n"
        + "function setTimeout() {}\n"
        + fn
        + "\n"
        + f"showRegistrationMarquee({name_js}, {station}, null, 'fan_bike');\n"
        + "console.log(toastEl.innerHTML);"
    )
    return _run_node(script)


def test_registration_marquee_shows_station_fallback_for_anonymous_athlete():
    html = _run_show_registration_marquee("null", 3)
    assert "Station 3" in html
    assert "null" not in html


def test_registration_marquee_still_shows_real_name_when_present():
    html = _run_show_registration_marquee('"Alice"', 3)
    assert "Alice" in html


# ---------------------------------------------------------------------------
# 4. classAdmin.html -- buildStationStatusHtml distinguishes "nobody has
# registered" from "someone registered anonymously" via the raw station's
# registered flag, not athleteName truthiness.
# ---------------------------------------------------------------------------


def _run_build_station_status_html(rows_js: str, stations_by_id_js: str = None) -> str:
    source = _read("classAdmin.html")
    fn = _strip_js_comments(_extract_function(source, "buildStationStatusHtml"))
    call = (
        f"buildStationStatusHtml({rows_js}, {stations_by_id_js})"
        if stations_by_id_js is not None
        else f"buildStationStatusHtml({rows_js})"
    )
    script = (
        _real_t_from_messages(_en_us_messages())
        + _escape_html_stub()
        + fn
        + "\n"
        + f"console.log({call});"
    )
    return _run_node(script)


def _station_athlete_span_text(html: str) -> str:
    match = re.search(r'<span class="station-athlete">([^<]*)</span>', html)
    assert match, f"no station-athlete span found in: {html}"
    return match.group(1)


def test_station_status_shows_station_fallback_for_anonymous_registration():
    rows = json.dumps(
        [
            {
                "stationNumber": 3,
                "athleteName": None,
                "machineName": "BIKE_03",
                "isLive": True,
            }
        ]
    )
    stations_by_id = json.dumps({"3": {"registered": True}})
    html = _run_build_station_status_html(rows, stations_by_id)
    assert _station_athlete_span_text(html) == "Station 3"


def test_station_status_still_shows_no_athlete_placeholder_when_truly_unregistered():
    """Regression guard on the fix itself: a station nobody has registered
    at all (registered omitted/false) must keep showing the existing
    no-athlete placeholder, not "Station N" -- these are different facts."""
    rows = json.dumps(
        [
            {
                "stationNumber": 2,
                "athleteName": None,
                "machineName": None,
                "isLive": False,
            }
        ]
    )
    stations_by_id = json.dumps({"2": {"registered": False}})
    html = _run_build_station_status_html(rows, stations_by_id)
    assert (
        _station_athlete_span_text(html)
        == _en_us_messages()["classAdmin.station_no_athlete"]
    )


def test_station_status_still_shows_real_name_when_present():
    rows = json.dumps(
        [
            {
                "stationNumber": 3,
                "athleteName": "Alice",
                "machineName": "BIKE_03",
                "isLive": True,
            }
        ]
    )
    stations_by_id = json.dumps({"3": {"registered": True}})
    html = _run_build_station_status_html(rows, stations_by_id)
    assert _station_athlete_span_text(html) == "Alice"


# ---------------------------------------------------------------------------
# 5. gameAdmin.html -- the Station Status list's per-station row.
# ---------------------------------------------------------------------------


def _load_game_admin_en_us_dictionary() -> dict:
    source = _read("gameAdmin.html")
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)
    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)
    js = source[const_start : zh_close + 1] + ";"
    js += "\nconsole.log(JSON.stringify(dictionaries['en-US']));"
    result = subprocess.run(
        ["node", "-e", js], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def _run_game_admin_assigned_stations(
    stations_js: str, station_health_js: str = "[]"
) -> str:
    source = _read("gameAdmin.html")
    script = _script_body(source)
    get_health_fn = _strip_js_comments(_extract_function(script, "getStationHealth"))
    body = _strip_js_comments(
        _extract_braced(script, "const assigned = keys.map((key) => ")
    )
    harness = (
        _real_t_from_messages(_load_game_admin_en_us_dictionary())
        + _escape_html_stub()
        + f"const state = {{ readiness: {{ station_health: {station_health_js} }} }};\n"
        + get_health_fn
        + "\n"
        + "const window = { location: { origin: 'http://test.local' } };\n"
        + f"const stations = {stations_js};\n"
        + "const keys = Object.keys(stations).sort((a, b) => Number(a) - Number(b));\n"
        + f"const assigned = keys.map((key) => {body}).join('');\n"
        + "console.log(assigned);"
    )
    return _run_node(harness)


def test_game_admin_station_list_shows_station_fallback_for_anonymous_registration():
    stations = json.dumps(
        {"3": {"equipment_type": "fan_bike", "athlete_name": None, "registered": True}}
    )
    html = _run_game_admin_assigned_stations(stations)
    assert "Station 3" in html
    assert ">null<" not in html


def test_game_admin_station_list_still_shows_no_athlete_placeholder_when_truly_unregistered():
    stations = json.dumps(
        {"2": {"equipment_type": "fan_bike", "athlete_name": None, "registered": False}}
    )
    html = _run_game_admin_assigned_stations(stations)
    en_us = _load_game_admin_en_us_dictionary()
    assert en_us["text.no_athlete"] in html
    # Station 2 IS in the row (the station-number heading always shows it),
    # so the assertion pins the placeholder text specifically, not absence
    # of the digit.
    assert en_us["text.no_athlete"] in html.split("row-meta")[1]


def test_game_admin_station_list_still_shows_real_name_when_present():
    stations = json.dumps(
        {
            "3": {
                "equipment_type": "fan_bike",
                "athlete_name": "Alice",
                "registered": True,
            }
        }
    )
    html = _run_game_admin_assigned_stations(stations)
    assert "Alice" in html


# ---------------------------------------------------------------------------
# 6. signup.html -- the name field is optional: no `required` attribute,
# relabeled in every locale, and submission no longer blocks on an empty
# name (it sends null instead of refusing to submit).
# ---------------------------------------------------------------------------


def test_signup_athlete_name_input_has_no_required_attribute():
    source = _read("signup.html")
    input_tag_match = re.search(r'<input[^>]*id="athlete-name"[^>]*>', source)
    assert input_tag_match, "athlete-name input not found"
    assert "required" not in input_tag_match.group(0)


def _signup_locale_label_values() -> dict:
    """Extract signup.athlete_name's value from fallbackMessages (en-US)
    and every signupLocaleOverrides entry, by loading the real inline
    dictionaries under node (not a text grep), so this proves the actual
    values a browser would use, in all six locales."""
    source = _read("signup.html")
    script = _script_body(source)
    fallback_start = script.index("const fallbackMessages = {")
    fallback_open = script.index("{", fallback_start)
    fallback_close = _matching_brace_end(script, fallback_open)
    overrides_start = script.index("const signupLocaleOverrides = {")
    overrides_open = script.index("{", overrides_start)
    overrides_close = _matching_brace_end(script, overrides_open)

    js = (
        script[fallback_start : fallback_close + 1]
        + ";\n"
        + script[overrides_start : overrides_close + 1]
        + ";\n"
        "const result = { 'en-US': fallbackMessages['signup.athlete_name'] };\n"
        "for (const locale of Object.keys(signupLocaleOverrides)) {\n"
        "  result[locale] = signupLocaleOverrides[locale]['signup.athlete_name']"
        " || fallbackMessages['signup.athlete_name'];\n"
        "}\n"
        "console.log(JSON.stringify(result));"
    )
    result = subprocess.run(
        ["node", "-e", js], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}\nScript:\n{js}"
    return json.loads(result.stdout)


def test_signup_athlete_name_label_reads_optional_in_all_six_locales():
    values = _signup_locale_label_values()
    assert set(values.keys()) == {"en-US", "zh-TW", "it", "fr", "de-CH", "sv"}
    # Every locale must mark the field optional in its own language -- none
    # left as a copy of a stale mandatory-sounding label, and none identical
    # to another locale's translation (would indicate a missed translation).
    expectations = {
        "en-US": "optional",
        "it": "facoltativo",
        "fr": "facultatif",
        "de-CH": "optional",
        "sv": "valfritt",
    }
    for locale, needle in expectations.items():
        assert (
            needle.lower() in values[locale].lower()
        ), f"{locale} athlete-name label does not read as optional: {values[locale]!r}"
    assert "選填" in values["zh-TW"]
    # No two locales share the exact same (untranslated) string.
    assert len(set(values.values())) == len(values)


def _run_submit_form(name_value: str, avatar_base64: str = "null") -> dict:
    """Executes the real submitForm() under node with the station taken
    from the query string (hasQueryStation branch, so the station-select
    dropdown never needs to be stubbed), proving an empty name reaches
    fetch() as null instead of the submission being silently dropped."""
    source = _read("signup.html")
    script = _script_body(source)
    fn = _strip_js_comments(_extract_function(script, "submitForm", async_fn=True))
    harness = (
        "let preventDefaultCalled = false;\n"
        "const event = { preventDefault: () => { preventDefaultCalled = true; } };\n"
        "let avatarProcessing = false;\n"
        f"let avatarBase64 = {avatar_base64};\n"
        "const hasQueryStation = true;\n"
        "const station = 3;\n"
        f"const athleteNameInput = {{ value: {json.dumps(name_value)} }};\n"
        "const teamNameInput = { value: '' };\n"
        "const successMsg = { style: {}, innerText: '' };\n"
        "const errorMsg = { style: {}, innerText: '' };\n"
        "const submitBtn = { disabled: false };\n"
        "const btnText = { style: {} };\n"
        "const btnSpinner = { style: {} };\n"
        "const customPreview = { style: {}, innerText: '' };\n"
        "const defaultAvatarOption = {};\n"
        "function setActiveAvatarOption() {}\n"
        "function t(key) { return key; }\n"
        "let fetchCalls = [];\n"
        "function fetch(url, options) {\n"
        "  fetchCalls.push({ url, body: JSON.parse(options.body) });\n"
        "  return Promise.resolve({ ok: true, json: async () => ({}) });\n"
        "}\n"
        + fn
        + "\n"
        + "submitForm(event).then(() => {\n"
        + "  console.log(JSON.stringify({ fetchCalls, preventDefaultCalled }));\n"
        + "});\n"
    )
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{harness}")
    return json.loads(result.stdout.strip())


def test_submit_form_sends_null_for_a_blank_name_instead_of_refusing_to_submit():
    result = _run_submit_form("   ")
    assert result["preventDefaultCalled"] is True
    assert len(result["fetchCalls"]) == 1
    body = result["fetchCalls"][0]["body"]
    assert body["athlete_name"] is None
    assert body["station_number"] == 3


def test_submit_form_still_sends_a_real_name_when_provided():
    result = _run_submit_form("  Alice  ")
    body = result["fetchCalls"][0]["body"]
    assert body["athlete_name"] == "Alice"
