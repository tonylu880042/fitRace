import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _extract_get_race_stage_details(source: str) -> str:
    start = source.index("function getRaceStageDetails")
    # The function body is short; grab a generous window and trim at the
    # next top-level function declaration so we don't drag in unrelated code.
    window = source[start : start + 4000]
    end = window.index("\n    function renderRaceStageBanner")
    return window[:end]


def test_dashboard_race_stage_banner_has_no_hardcoded_waiting_for_setup():
    source = _read("index.html")
    stage_fn = _extract_get_race_stage_details(source)

    assert '"Waiting for setup"' not in stage_fn
    assert "stage.waiting_setup" in stage_fn


def test_dashboard_race_stage_banner_routes_literals_through_t():
    source = _read("index.html")
    stage_fn = _extract_get_race_stage_details(source)

    # A representative sample of the literals that used to be hardcoded
    # English inside getRaceStageDetails(). None of these should appear as
    # bare string literals any more -- they should all be t("stage.*") calls.
    hardcoded_literals = [
        '"Venue Display"',
        '"Countdown"',
        '"3, 2, 1, Go"',
        '"Configure the race from Game Admin."',
        '"Ready"',
        '"Result"',
        '"Live Race"',
    ]
    for literal in hardcoded_literals:
        assert (
            literal not in stage_fn
        ), f"{literal} is still hardcoded in getRaceStageDetails"

    assert "stage.venue_display" in stage_fn
    assert "stage.countdown" in stage_fn
    assert "stage.go" in stage_fn
    assert "stage.configure_hint" in stage_fn
    assert "stage.ready" in stage_fn
    assert "stage.result" in stage_fn
    assert "stage.live_race" in stage_fn


def test_dashboard_rank_tooltips_use_i18n_not_hardcoded_chinese():
    source = _read("index.html")

    assert 'title="冠軍"' not in source
    assert 'title="亞軍"' not in source
    assert 'title="季軍"' not in source
    assert source.count('t("rank.champion")') >= 2
    assert source.count('t("rank.second")') >= 2
    assert source.count('t("rank.third")') >= 2


def test_system_admin_no_hardcoded_disconnected_fallback():
    source = _read("systemAdmin.html")

    # The old code inlined "Disconnected"/"Checking" directly in the
    # renderWifiStatus logic instead of going through t(). Those two call
    # sites should now read from the dictionaries via t("wifi.*"). The
    # literal "Disconnected" is still legitimately present as the English
    # dictionary *value* for the wifi.disconnected key, so we assert against
    # the specific hardcoded patterns rather than the bare string.
    assert 'unknown: "Disconnected"' not in source
    assert '|| "Checking"' not in source
    assert ': "Disconnected";' not in source
    assert 't("wifi.disconnected")' in source
    assert 't("wifi.checking")' in source


def test_system_admin_update_state_pill_not_raw_enum():
    source = _read("systemAdmin.html")

    # The old code rendered the raw enum text with underscores swapped for
    # spaces and no translation at all.
    assert "update.state_never_checked" in source
    assert (
        "update.no_check_completed" in source or "update.detail_never_checked" in source
    )


def test_system_admin_idle_only_pill_has_i18n_key():
    source = _read("systemAdmin.html")
    match = re.search(r'<span class="pill" id="power-lock"[^>]*>([^<]*)</span>', source)
    assert match, "power-lock pill not found"
    assert "data-i18n=" in source.split('id="power-lock"')[1].split(">")[0]


def test_system_admin_header_subtitle_has_i18n_key():
    source = _read("systemAdmin.html")
    assert (
        "<p>Edge node status · software updates · system maintenance</p>" not in source
    )
    assert (
        "data-i18n=" in source[source.index("<p") : source.index("<p") + 2000]
        or "header.subtitle" in source
    )


def test_game_admin_subtitle_has_data_i18n():
    source = _read("gameAdmin.html")
    assert "<p>Race setup and coach-operated event control</p>" not in source
    assert "data-i18n" in source
    # Grab the subtitle <p> line specifically and confirm it carries the attribute.
    subtitle_line = next(
        line for line in source.splitlines() if "coach-operated event control" in line
    )
    assert "data-i18n=" in subtitle_line


def test_game_admin_uses_two_separate_buttons_for_save_and_start_steps():
    """Superseded design: a single id="btn-race-action" button used to swap
    its own label between Save Race and Start Race depending on dirty
    state -- dangerous mid-race, since a mis-timed click on stale UI state
    could start the race instead of saving a settings tweak. It is now two
    independent buttons, gated separately (save on unsaved changes, start
    on readiness). Full coverage of the split (disabled-state logic,
    countdown/processing text, guard clauses) lives in
    tests/unit/hub/test_game_admin_race_control_restructure.py; this test
    keeps the historical i18n-value assertions that this file already made
    for button.save_race/button.start_race, updated for the new wiring.
    """
    source = _read("gameAdmin.html")

    assert 'id="btn-race-action"' not in source
    assert "handleRaceAction" not in source
    assert 'id="btn-save-race"' in source
    assert 'onclick="saveRaceConfig()"' in source
    assert 'id="btn-start-race"' in source
    assert 'onclick="startRaceAction()"' in source
    assert 'data-i18n="button.save_race"' in source
    assert 'data-i18n="button.start_race"' in source
    assert '"button.save_race": "Save Race"' in source
    assert '"button.save_race": "儲存比賽"' in source
    assert '"button.start_race": "Start Race"' in source
    assert '"button.start_race": "開始比賽"' in source
    assert "button.save_and_start_race" not in source
    assert 'id="btn-configure"' not in source

    save_start = source.index("async function saveRaceConfig()")
    save_end = source.index("async function startRaceAction()", save_start)
    save_source = source[save_start:save_end]
    assert "await configureRace();" in save_source

    start_start = source.index("async function startRaceAction()")
    start_end = source.index("async function startRace()", start_start)
    start_source = source[start_start:start_end]
    assert "await startRace();" in start_source

    configure_start = source.index("async function configureRace()")
    configure_end = source.index(
        "async function setLeaderboardDisplayMode", configure_start
    )
    configure_source = source[configure_start:configure_end]
    assert "state.raceConfigDirty = false;" in configure_source

    render_start = source.index("function renderRaceActionButtons()")
    render_end = source.index("function readinessStatusClass", render_start)
    render_source = source[render_start:render_end]
    assert "needsSave" in render_source
    assert 'raceState !== "READY"' in render_source
    assert "!readinessReady" in render_source
    assert 't("button.save_race")' in render_source
    assert 't("button.start_race")' in render_source

    assert source.count("markRaceConfigDirty()") >= 6
    assert "if (config && !state.raceConfigDirty)" in source


def test_game_admin_labels_individual_race_as_anonymous_optional():
    source = _read("gameAdmin.html")

    assert '"option.individual": "Individual Race (Anonymous Allowed)"' in source
    assert '"option.individual": "個人賽（可匿名參加）"' in source


def test_game_admin_and_system_admin_inline_dictionaries_stay_symmetric():
    for name in ("gameAdmin.html", "systemAdmin.html"):
        source = _read(name)
        en_start = source.index('"en-US": {')
        zh_start = source.index('dictionaries["zh-TW"] = {')
        en_block = source[en_start:zh_start]
        zh_block = source[zh_start : zh_start + 6000]

        en_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', en_block))
        zh_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', zh_block))
        missing_from_zh = en_keys - zh_keys
        assert (
            not missing_from_zh
        ), f"{name}: keys missing from zh-TW dict: {missing_from_zh}"


# ---------------------------------------------------------------------------
# Regression guard: no hardcoded English left in the JS of the two hub admin
# pages, so nothing shows English while the UI is set to zh-TW.
#
# The scan is intentionally narrow (mirrors what a human reviewer would flag):
# it only looks at the literal argument of the specific calls that put text
# on screen -- .textContent/.innerText/.innerHTML/.placeholder assignments,
# and confirm(/alert(/setMessage( calls -- where that literal, once you take
# out ${...} interpolations and HTML tags, still contains at least two
# consecutive alphabetic words. That is deliberately narrower than "every
# English string anywhere in the file": values built up via helper functions,
# array/object lookup tables (e.g. modeText, previewRows), or template
# literals assigned to an intermediate variable before being interpolated
# elsewhere are not inspected here -- those are reviewed by hand instead,
# since a regex can't safely evaluate them.

_STRING_LITERAL = r'(`(?:[^`\\]|\\.)*`|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'

_ASSIGNMENT_PATTERNS = [
    re.compile(r"\.textContent\s*=\s*" + _STRING_LITERAL),
    re.compile(r"\.innerText\s*=\s*" + _STRING_LITERAL),
    re.compile(r"\.innerHTML\s*=\s*" + _STRING_LITERAL),
    re.compile(r"\.placeholder\s*=\s*" + _STRING_LITERAL),
    re.compile(r"confirm\(\s*" + _STRING_LITERAL),
    re.compile(r"alert\(\s*" + _STRING_LITERAL),
    re.compile(r"setMessage\([^,]+,\s*" + _STRING_LITERAL),
]

_ENGLISH_WORDS_RE = re.compile(r"[A-Za-z]+\s+[A-Za-z]+")

# Literals that are legitimately not translatable: product/version strings,
# and (for the game-admin preview rows) the fictional team names, which are
# proper nouns for a demo leaderboard, not UI copy.
_ALLOWLIST = {
    '`Hub v${health.version || "--"}`',  # product name + version prefix
    '"Hub v--"',  # same, offline fallback
}


def _extract_script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _blank_dictionary_blocks(script: str) -> str:
    """Blank out the two inline dictionary object literals (brace-matched)
    so their (translated) string values are never mistaken for hardcoded
    UI text, while preserving line numbers for readable failure output."""
    out = script
    for marker in ("const dictionaries = {", 'dictionaries["zh-TW"] = {'):
        idx = out.index(marker)
        brace_start = out.index("{", idx)
        depth = 0
        i = brace_start
        while i < len(out):
            if out[i] == "{":
                depth += 1
            elif out[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        brace_end = i
        blanked = re.sub(r"[^\n]", " ", out[brace_start : brace_end + 1])
        out = out[:brace_start] + blanked + out[brace_end + 1 :]
    return out


def _strip_interpolations(literal: str) -> str:
    """Remove ${...} interpolation spans (brace-matched) from a template
    literal so variable/expression names aren't mistaken for English words."""
    out = []
    i = 0
    while i < len(literal):
        if literal[i : i + 2] == "${":
            depth = 1
            j = i + 2
            while j < len(literal) and depth > 0:
                if literal[j] == "{":
                    depth += 1
                elif literal[j] == "}":
                    depth -= 1
                j += 1
            i = j
        else:
            out.append(literal[i])
            i += 1
    return "".join(out)


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]*>", " ", text)


def _find_hardcoded_english(script: str) -> list:
    """Return a list of (line_number, literal) for every offending literal."""
    blanked = _blank_dictionary_blocks(script)
    offenders = []
    seen = set()
    for pattern in _ASSIGNMENT_PATTERNS:
        for match in pattern.finditer(blanked):
            literal = match.group(1)
            if literal in _ALLOWLIST or literal in seen:
                continue
            plain_text = _strip_html_tags(_strip_interpolations(literal))
            if _ENGLISH_WORDS_RE.search(plain_text):
                seen.add(literal)
                line = blanked[: match.start()].count("\n") + 1
                offenders.append((line, literal))
    return sorted(offenders)


def test_game_admin_and_system_admin_js_has_no_hardcoded_english_ui_strings():
    all_offenders = {}
    for name in ("gameAdmin.html", "systemAdmin.html"):
        script = _extract_script(_read(name))
        offenders = _find_hardcoded_english(script)
        if offenders:
            all_offenders[name] = offenders
    assert not all_offenders, (
        "Hardcoded English text found in JS that would still show in zh-TW: "
        f"{all_offenders}"
    )
