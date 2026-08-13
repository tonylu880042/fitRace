"""The dashboard's individual-athlete metric labels already route through
`t()`, but the team/podium/sprint-board/race-track render paths did not --
they built their labels, name fallbacks, and status text from hardcoded
English string literals (`"Team distance"`, `"Athlete"`, `member${n === 1 ?
"" : "s"}`, etc.). On a zh-TW venue screen those render as mixed Chinese and
English. This module pins that every offending literal has been routed
through `t()` against real locale keys.

Comment-stripping mirrors tests/unit/hub/test_operator_name_equipment_id_rung.py
(a comment containing the same substring must not satisfy the absence
assertions). The executing tests mirror
tests/unit/hub/test_dashboard_station_machine_name.py: they extract a real
function's source by brace-depth matching and run it under `node`, proving
the returned value is actually produced by a `t()` call rather than merely
that the string `t(` appears somewhere nearby.
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


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read_index()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Mirrors tests/unit/hub/test_dashboard_station_machine_name.py."""
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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


# -- 1. Absence test: every offending literal from the inventory must be gone
# from the comment-stripped inline <script>. Each entry is copied verbatim
# from the pre-fix source so a stray comment mentioning the same text can't
# satisfy the check.
OFFENDING_LITERALS = [
    # formatTeamScore labels
    'label: "Team distance"',
    'label: "Avg progress"',
    'label: "Team kcal"',
    'label: "Team power"',
    'label: "Team score"',
    # formatSprintSignal labels
    'label: "Team peak"',
    'label: "Avg kph"',
    'label: "Current power"',
    'label: "kph"',
    # completion / policy / scoring enum text rendered raw
    '"all members finish" : "aggregate finish"',
    'row.kind === "team"\n          ? String(row.raw.completion_policy || "aggregate").replace(/_/g, " ")',
    # getTeamBattleStatus
    '? "All in"',
    'member${missingCount === 1 ? "" : "s"} to finish',
    "${progress.toFixed(0)}% team progress`;",
    # Solo / VS
    '"team-battle-vs">Solo<',
    '"team-battle-vs">VS<',
    # sprint-board stat labels
    'statALabel: "Finished"',
    'statBLabel: "Average"',
    'statCLabel: "Distance"',
    'statALabel: "Power"',
    'statBLabel: "Distance"',
    'statCLabel: "Kcal"',
    "<span>Progress</span>",
    '"team" ? "Policy" : "Node"',
    # station pill
    "STATION ${escapeHtml(node.station_number)}",
    # team-leaderboard metric labels
    '<div class="metric-lbl">Team progress</div>',
    '<div class="metric-lbl">Finished</div>',
    # aria-labels / alt / title
    'aria-label="Race winners"',
    'aria-label="Team race winners"',
    'alt="Register QR"',
    'title="Finish"',
    # competition label
    '? "Team" : "Individual"',
    # name / group fallbacks
    '"Athlete"',
    '|| "Team"',
    '"Unassigned"',
    "} athletes`",
    "} finished`",
    '${station || "Node"}',
    "}s finish`",
    "}% progress`",
    # race state heading + edge node card (second i18n pass)
    '"current-state-lbl").innerText = currentState;',
    'if (diffSec < 2) return "now";',
    "if (diffSec < 60) return `${diffSec}s`;",
    "return `${minutes}m`;",
    'stream.node_id || "UNBOUND"',
    'stream.status || "unknown"',
    "<strong>0 streams</strong>",
    'aria-label="${isOnline ? "online" : "offline"}"',
    'node.edge_node_id || "unknown-edge"',
    "· last ${formatRelativeAge(",
]


def test_offending_literals_absent_from_index_html():
    script = _stripped_script()
    for literal in OFFENDING_LITERALS:
        assert literal not in script, f"{literal!r} is still hardcoded in index.html"


def test_no_english_pluralization_branch_left_in_source():
    script = _stripped_script()
    assert 'missingCount === 1 ? "" : "s"' not in script
    assert "count: missingCount.toFixed(0)" in script


# -- 2. Executing tests: run the real functions under node with a `t` stub
# that returns a recognisable marker, proving the label genuinely comes from
# a t() call rather than a literal that happens to look similar.


def _t_stub() -> str:
    return "const t = (key) => `T[${key}]`;\n"


def _metric_number_stub() -> str:
    return (
        "const metricNumber = (value, fallback = 0) => {\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fallback;\n"
        "};\n"
    )


def _run_format_team_score(team_js: str, race_type: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatTeamScore"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + fn
        + "\n"
        + f"console.log(JSON.stringify(formatTeamScore({team_js}, {json.dumps(race_type)})));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def test_format_team_score_distance_label_routes_through_t():
    result = _run_format_team_score(
        '{score_label: "distance_m", score_value: 500}', "distance"
    )
    assert result["label"] == "T[team.distance_label]"


def test_format_team_score_avg_progress_label_routes_through_t():
    result = _run_format_team_score(
        '{score_label: "progress", score_value: 42}', "distance"
    )
    assert result["label"] == "T[team.avg_progress_label]"


def test_format_team_score_power_label_routes_through_t():
    result = _run_format_team_score("{score_value: 300}", "max_power")
    assert result["label"] == "T[team.power_label]"


def _run_get_team_battle_status(team_js: str, progress: float) -> dict:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "getTeamBattleStatus"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + fn
        + "\n"
        + f"console.log(JSON.stringify(getTeamBattleStatus({team_js}, {progress})));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def test_team_battle_status_finished_label_routes_through_t():
    result = _run_get_team_battle_status(
        "{member_count: 4, finished_count: 4, team_finished: true}", 100
    )
    assert result["statusLabel"] == "T[team.finished_label]"


def test_team_battle_status_all_in_label_routes_through_t():
    result = _run_get_team_battle_status(
        "{member_count: 4, finished_count: 4, team_finished: false}", 100
    )
    assert result["statusLabel"] == "T[team.status_all_in]"


def _run_get_team_battle_status_with_param_capture(
    team_js: str, progress: float
) -> dict:
    """Same as _run_get_team_battle_status, but the `t` stub also echoes the
    params object it was called with -- so we can prove `count` reaches t()
    as a plain number, not a pre-formatted "N member(s)" string built by
    JS-side pluralization logic."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "getTeamBattleStatus"))
    script = (
        "const t = (key, params) => ({ key, params: params || {} });\n"
        + _metric_number_stub()
        + fn
        + "\n"
        + f"console.log(JSON.stringify(getTeamBattleStatus({team_js}, {progress})));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def test_team_battle_status_members_remaining_passes_plain_count_not_pluralized_string():
    result = _run_get_team_battle_status_with_param_capture(
        "{member_count: 4, finished_count: 1, team_finished: false, "
        'completion_policy: "all_members"}',
        25,
    )
    status = result["statusLabel"]
    assert status["key"] == "team.status_members_remaining"
    assert status["params"] == {"count": "3"}


def test_team_battle_status_progress_routes_through_t():
    result = _run_get_team_battle_status(
        "{member_count: 4, finished_count: 1, team_finished: false, "
        'completion_policy: "aggregate"}',
        37,
    )
    assert result["statusLabel"] == "T[team.status_progress]"


def _run_format_sprint_signal(row_js: str, race_type: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatSprintSignal"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + fn
        + "\n"
        + f"console.log(JSON.stringify(formatSprintSignal({row_js}, {json.dumps(race_type)})));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def test_format_sprint_signal_team_peak_label_routes_through_t():
    result = _run_format_sprint_signal(
        '{kind: "team", raw: {max_power_watts: 500}}', "max_power"
    )
    assert result["label"] == "T[team.peak_power_label]"


def test_format_sprint_signal_individual_kph_label_routes_through_t():
    result = _run_format_sprint_signal(
        '{kind: "individual", raw: {instantaneous_speed_kph: 30}}', "distance"
    )
    assert result["label"] == "T[metric.speed]"


# -- 3. Locale test: every new key exists in all six locale files, and the
# zh-TW value is genuinely translated (contains at least one CJK char) --
# catching "key added but English left in the zh-TW file".

NEW_LOCALE_KEYS = [
    "fallback.team",
    "fallback.unassigned_team",
    "fallback.node",
    "team.distance_label",
    "team.avg_progress_label",
    "team.kcal_label",
    "team.power_label",
    "team.score_label",
    "team.peak_power_label",
    "team.avg_speed_label",
    "team.completion_all_members",
    "team.completion_aggregate",
    "team.policy_all_members",
    "team.policy_aggregate",
    "team.policy_label",
    "team.scoring_average",
    "team.scoring_total",
    "team.scoring_method_label",
    "team.athlete_count",
    "team.finished_count",
    "team.finished_label",
    "team.progress_label",
    "team.status_all_in",
    "team.status_members_remaining",
    "team.status_progress",
    "team.solo_label",
    "team.vs_label",
    "leaderboard.seconds_finish",
    "leaderboard.percent_progress",
    "leaderboard.finish_flag",
    "podium.aria_individual",
    "podium.aria_team",
    "qr.register_alt",
    # race state heading + edge node card (second i18n pass)
    "state.idle",
    "state.ready",
    "state.running",
    "state.stopped",
    "status.unknown",
    "edge.unknown_node",
    "edge.zero_streams",
    "edge.last_seen",
    "time.now",
    "time.seconds_short",
    "time.minutes_short",
]

_CJK_RE = re.compile(r"[一-鿿]")


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as file:
        return json.load(file)


def test_new_keys_exist_in_every_locale():
    for locale in ("zh-TW", "en-US", "de-CH", "fr", "it", "sv"):
        messages = _load_locale(locale)
        for key in NEW_LOCALE_KEYS:
            assert key in messages, f"{key} missing from {locale}.json"


def test_new_keys_zh_tw_values_are_genuinely_chinese():
    zh = _load_locale("zh-TW")
    for key in NEW_LOCALE_KEYS:
        value = zh[key]
        assert _CJK_RE.search(
            value
        ), f"{key} zh-TW value has no CJK character: {value!r}"


# -- 4. Referenced-key guard: t() is `messages[key] || key`, so a key that
# does not exist in the locale files silently renders the RAW KEY on the
# venue projector instead of raising an error. Nothing else in the suite
# checks that every key index.html *asks for* actually exists -- the tests
# above only check that specific literals were replaced, not that every
# t()/data-i18n reference in the file resolves. This collects every key
# index.html references (both `t("...")` calls in the inline <script> and
# `data-i18n`/`data-i18n-alt` attribute values in the HTML) and asserts each
# one is defined in en-US.json. Key symmetry across the other five locale
# files is already enforced by test_all_supported_locales_have_matching_keys
# in tests/unit/hub/test_i18n_locales.py, so checking en-US alone here is
# sufficient to guarantee every referenced key resolves in every locale.
#
# The `t\(` pattern uses a negative lookbehind so it only matches `t(` as a
# standalone call -- without it, `document.createElement("div")` and
# `createElement("span")` also match on their trailing `t("...")`-shaped
# tail, producing phantom missing keys "div" and "span" that don't exist
# because they were never real key references.
_T_CALL_RE = re.compile(r'(?<![A-Za-z0-9_$])t\(\s*"([^"]+)"')
_DATA_I18N_ATTR_RE = re.compile(r'data-i18n(?:-alt)?="([^"]+)"')


def _referenced_i18n_keys() -> set:
    script_keys = set(_T_CALL_RE.findall(_stripped_script()))
    attr_keys = set(_DATA_I18N_ATTR_RE.findall(_read_index()))
    return script_keys | attr_keys


def test_every_referenced_i18n_key_exists_in_en_us_locale():
    keys = _referenced_i18n_keys()
    # Sanity floor: if the extraction regex ever breaks (e.g. index.html
    # stops using inline t("...") calls the way it does today), this test
    # must not silently pass with zero keys collected.
    assert len(keys) >= 100, (
        f"only {len(keys)} i18n keys extracted from index.html -- "
        "the extraction regex may be broken"
    )

    en = _load_locale("en-US")
    missing = sorted(key for key in keys if key not in en)
    assert (
        not missing
    ), f"index.html references i18n keys missing from en-US.json: {missing}"


# -- 5. Race state heading + Edge Nodes card (second i18n pass). Same
# executing-node technique as section 2: extract the real function by
# brace-depth matching and run it under `node` with a `t` stub that returns
# a recognisable `T[key]` marker, so a match proves the string genuinely
# came from a `t()` call. Each render-path test additionally stubs
# `document.getElementById` with a plain recording object and asserts the
# marker actually reaches that object's `innerText`/`innerHTML` -- not just
# that the pure helper returns the right thing in isolation, which is the
# defect class the last four review rounds kept finding (a correct helper
# whose result never reaches the DOM).


def _run_race_state_label(state_js: str) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "raceStateLabel"))
    script = _t_stub() + fn + "\n" + f"console.log(raceStateLabel({state_js}));"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout.strip()


def test_race_state_label_idle_routes_through_t():
    assert _run_race_state_label('"IDLE"') == "T[state.idle]"


def test_race_state_label_ready_routes_through_t():
    assert _run_race_state_label('"READY"') == "T[state.ready]"


def test_race_state_label_running_routes_through_t():
    assert _run_race_state_label('"RUNNING"') == "T[state.running]"


def test_race_state_label_stopped_routes_through_t():
    assert _run_race_state_label('"STOPPED"') == "T[state.stopped]"


def test_race_state_label_unrecognised_state_falls_back_to_raw_value():
    """An unknown state must still surface -- silently returning "" would
    blank the venue heading, which is worse than showing the raw enum."""
    result = _run_race_state_label('"BOGUS_STATE"')
    assert result == "BOGUS_STATE"
    assert not result.startswith("T[")


def _run_render_current_state_label(state_js: str) -> dict:
    """Executes the actual DOM-writing wrapper (not just the pure helper),
    with document.getElementById stubbed to hand back a plain recording
    object -- proving the translated value genuinely lands on
    el.innerText, not merely that raceStateLabel() computes it correctly
    off to the side."""
    source = _read_index()
    label_fn = _strip_js_comments(_extract_function(source, "raceStateLabel"))
    render_fn = _strip_js_comments(_extract_function(source, "renderCurrentStateLabel"))
    script = (
        _t_stub()
        + label_fn
        + "\n"
        + render_fn
        + "\n"
        + "const el = {};\n"
        + 'const document = { getElementById: (id) => (id === "current-state-lbl" ? el : null) };\n'
        + f"renderCurrentStateLabel({state_js});\n"
        + "console.log(JSON.stringify({ innerText: el.innerText }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def test_render_current_state_label_writes_translated_value_to_element():
    result = _run_render_current_state_label('"RUNNING"')
    assert result["innerText"] == "T[state.running]"


def test_render_current_state_label_writes_translated_value_for_stopped():
    result = _run_render_current_state_label('"STOPPED"')
    assert result["innerText"] == "T[state.stopped]"


def _run_format_relative_age(epoch_expr: str) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatRelativeAge"))
    script = _t_stub() + fn + "\n" + f"console.log(formatRelativeAge({epoch_expr}));"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout.strip()


def test_format_relative_age_now_routes_through_t():
    assert _run_format_relative_age("Date.now()") == "T[time.now]"


def test_format_relative_age_seconds_routes_through_t():
    assert _run_format_relative_age("Date.now() - 30000") == "T[time.seconds_short]"


def test_format_relative_age_minutes_routes_through_t():
    assert _run_format_relative_age("Date.now() - 5 * 60000") == "T[time.minutes_short]"


def _run_render_edge_nodes(nodes_js: str) -> dict:
    """Executes the real renderEdgeNodes() with document.getElementById
    stubbed to plain recording objects, proving the translated markers
    actually land in the rendered innerHTML/innerText -- not just that
    t("edge.unbound") etc. appear as literal calls somewhere in the source
    (that grep-only style of check is what let a computed-but-unused
    translation slip through in earlier rounds)."""
    source = _read_index()
    format_age_fn = _strip_js_comments(_extract_function(source, "formatRelativeAge"))
    render_fn = _strip_js_comments(_extract_function(source, "renderEdgeNodes"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + 'const escapeHtml = (v) => String(v ?? "");\n'
        + format_age_fn
        + "\n"
        + render_fn
        + "\n"
        + "const listEl = {};\n"
        + "const summaryEl = {};\n"
        + "const captionEl = {};\n"
        + "const document = { getElementById: (id) => ({\n"
        + '  "edge-node-list": listEl,\n'
        + '  "edge-node-summary": summaryEl,\n'
        + '  "edge-node-caption": captionEl,\n'
        + "}[id] || null) };\n"
        + f"renderEdgeNodes({nodes_js});\n"
        + "console.log(JSON.stringify({ listHtml: listEl.innerHTML, summary: summaryEl.innerText, caption: captionEl.innerText }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


_EDGE_NODES_FIXTURE = """[
  {
    edge_node_id: "e1",
    display_name: "Edge One",
    status: "online",
    ip: "1.2.3.4",
    available_channels: 2,
    max_ftms_connections: 5,
    last_seen_epoch_ms: Date.now(),
    equipment_streams: [{}]
  },
  {
    status: "offline",
    equipment_streams: []
  }
]"""


def test_render_edge_nodes_stream_name_and_status_fallbacks_are_translated():
    result = _run_render_edge_nodes(_EDGE_NODES_FIXTURE)
    html = result["listHtml"]
    assert "T[edge.unbound]" in html
    assert "T[status.unknown]" in html
    assert "UNBOUND" not in html
    assert ">unknown<" not in html


def test_render_edge_nodes_zero_streams_count_is_translated():
    result = _run_render_edge_nodes(_EDGE_NODES_FIXTURE)
    html = result["listHtml"]
    assert "T[edge.zero_streams]" in html
    assert "0 streams" not in html


def test_render_edge_nodes_unknown_node_name_is_translated():
    result = _run_render_edge_nodes(_EDGE_NODES_FIXTURE)
    html = result["listHtml"]
    assert "T[edge.unknown_node]" in html
    assert "unknown-edge" not in html


def test_render_edge_nodes_online_offline_aria_label_is_translated():
    result = _run_render_edge_nodes(_EDGE_NODES_FIXTURE)
    html = result["listHtml"]
    assert "T[connection.online]" in html
    assert "T[connection.offline]" in html
    assert 'aria-label="online"' not in html
    assert 'aria-label="offline"' not in html


def test_render_edge_nodes_last_seen_prefix_is_translated():
    result = _run_render_edge_nodes(_EDGE_NODES_FIXTURE)
    html = result["listHtml"]
    assert "T[edge.last_seen]" in html
    assert "· last " not in html
