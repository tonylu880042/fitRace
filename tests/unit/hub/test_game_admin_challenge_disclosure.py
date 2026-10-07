"""Challenge-mode disclosure for Game Admin
(docs/game_admin_progressive_disclosure_spec.md, 2026-10-07 addendum).

The source of truth for "challenge on" is the server state
`state.race.challenge_mode_enabled`, never the select's value. Visibility is
asserted (`field-collapsed` / `hidden`), not `.disabled`. Source is comment
stripped before extraction so a comment can never satisfy a test.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", code))


def _stripped_script() -> str:
    source = (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _match_end(source: str, open_idx: int, open_ch: str, close_ch: str) -> int:
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
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    paren_close = _match_end(source, start + len(marker) - 1, "(", ")")
    brace_open = source.index("{", paren_close)
    return source[start : _match_end(source, brace_open, "{", "}") + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout.strip()


_STUB = """
const mk = () => ({
  classList: {
    s: new Set(),
    toggle(c, f) { if (f) this.s.add(c); else this.s.delete(c); },
    contains(c) { return this.s.has(c); },
  },
  hidden: false,
  value: "",
  disabled: false,
  textContent: "",
  dataset: {},
});
const els = {};
const $ = (id) => (els[id] = els[id] || mk());
const collapsed = (id) => $(id).classList.contains("field-collapsed");
const t = (k, p = {}) => k + JSON.stringify(p);
const state = { race: { session_mode: "race", state: "IDLE" },
                roster: { counts: { pending: 0, loaded: 0, done: 0, absent: 0 } } };
"""

_FNS = (
    "rulesOpenAfterTransition",
    "disclosureState",
    "syncVisibility",
    "renderChallengeMode",
)


def _run(body: str, race_js: str = "{}", roster_counts: str | None = None) -> dict:
    src = _stripped_script()
    fns = "\n".join(_extract_function(src, n) for n in _FNS)
    roster = f"state.roster = {{ counts: {roster_counts} }};" if roster_counts else ""
    script = _STUB + fns + f"""
state.race = Object.assign({{ session_mode: "race", state: "IDLE" }}, {race_js});
{roster}
$("competition-mode").value = "individual";
{body}
"""
    return json.loads(_run_node(script))


_R3_FIELDS = [
    "challenge-duration-field",
    "challenge-min-result-field",
    "challenge-start-wait-field",
]


def _fields_collapsed() -> str:
    return f"{json.dumps(_R3_FIELDS)}.map(collapsed)"


# ---------------------------------------------------------------------------
# R3 -- sub-fields follow SERVER state, wired through the real call path
# ---------------------------------------------------------------------------


def test_sync_visibility_collapses_challenge_fields_when_server_state_is_off():
    out = _run(
        f"syncVisibility(); console.log(JSON.stringify({_fields_collapsed()}));",
        "{ challenge_mode_enabled: false }",
    )
    assert out == [True, True, True]


def test_sync_visibility_shows_challenge_fields_when_server_state_is_on():
    out = _run(
        f"syncVisibility(); console.log(JSON.stringify({_fields_collapsed()}));",
        "{ challenge_mode_enabled: true }",
    )
    assert out == [False, False, False]


def test_select_saying_true_does_not_override_server_state_off():
    out = _run(
        f"""$("challenge-mode-enabled").value = "true";
syncVisibility();
console.log(JSON.stringify({_fields_collapsed()}));""",
        "{ challenge_mode_enabled: false }",
    )
    assert out == [True, True, True]


def test_select_saying_false_does_not_override_server_state_on():
    out = _run(
        f"""$("challenge-mode-enabled").value = "false";
syncVisibility();
console.log(JSON.stringify({_fields_collapsed()}));""",
        "{ challenge_mode_enabled: true }",
    )
    assert out == [False, False, False]


def test_render_challenge_mode_drives_layout_from_server_state():
    out = _run(
        f"""const seen = [];
state.race.challenge_mode_enabled = true;
renderChallengeMode(); seen.push({_fields_collapsed()});
state.race.challenge_mode_enabled = false;
renderChallengeMode(); seen.push({_fields_collapsed()});
console.log(JSON.stringify(seen));""",
    )
    assert out == [[False, False, False], [True, True, True]]


# ---------------------------------------------------------------------------
# Markup structure
# ---------------------------------------------------------------------------

from html.parser import HTMLParser  # noqa: E402

_VOID = {"input", "br", "img", "meta", "link", "hr"}


class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent

    def inside(self, id_):
        node = self.parent
        while node is not None:
            if node.attrs.get("id") == id_:
                return True
            node = node.parent
        return False


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = _Node("root", [], None)
        self.cur = self.root
        self.by_id = {}

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        if "id" in node.attrs:
            self.by_id[node.attrs["id"]] = node
        if tag not in _VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent


def _tree() -> _Tree:
    parser = _Tree()
    parser.feed((STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8"))
    return parser


def test_each_challenge_input_sits_inside_its_own_field_wrapper():
    tree = _tree()
    for key in ("duration", "min-result", "start-wait"):
        assert tree.by_id[f"challenge-{key}"].inside(f"challenge-{key}-field"), key


# ---------------------------------------------------------------------------
# R9a / R9b / R9c -- Race Rules vs. the challenge note: exact complements
# ---------------------------------------------------------------------------


def _disclosure(**overrides):
    base = {
        "competitionMode": "individual",
        "challengeModeOn": False,
        "sessionMode": "race",
        "raceState": "IDLE",
        "hasRoster": False,
    }
    base.update(overrides)
    fn = _extract_function(_stripped_script(), "disclosureState")
    return json.loads(
        _run_node(
            f"{fn}\nconsole.log(JSON.stringify(disclosureState({json.dumps(base)})));"
        )
    )


def test_challenge_on_hides_rules_and_save_and_shows_note():
    s = _disclosure(challengeModeOn=True)
    assert (s["raceRules"], s["saveRace"], s["challengeRulesNote"]) == (
        False,
        False,
        True,
    )


def test_challenge_off_shows_rules_and_save_and_hides_note():
    s = _disclosure(challengeModeOn=False)
    assert (s["raceRules"], s["saveRace"], s["challengeRulesNote"]) == (
        True,
        True,
        False,
    )


def test_rules_and_note_are_exact_complements_in_every_input_combination():
    for mode in ("individual", "team", "relay"):
        for challenge in (True, False):
            for session in ("race", "class", None):
                for has_roster in (True, False):
                    s = _disclosure(
                        competitionMode=mode,
                        challengeModeOn=challenge,
                        sessionMode=session,
                        hasRoster=has_roster,
                    )
                    assert s["raceRules"] is not s["challengeRulesNote"], s
                    assert s["saveRace"] is s["raceRules"], s


def _applied(race_js: str) -> dict:
    return _run(
        """syncVisibility();
console.log(JSON.stringify({
  rulesHidden: $("rules-block").hidden,
  saveHidden: $("btn-save-race").hidden,
  noteCollapsed: collapsed("challenge-rules-note"),
}));""",
        race_js,
    )


def test_sync_visibility_hides_rules_block_and_save_button_when_challenge_on():
    out = _applied("{ challenge_mode_enabled: true }")
    assert out == {"rulesHidden": True, "saveHidden": True, "noteCollapsed": False}


def test_sync_visibility_shows_rules_block_and_save_button_when_challenge_off():
    out = _applied("{ challenge_mode_enabled: false }")
    assert out == {"rulesHidden": False, "saveHidden": False, "noteCollapsed": True}


def test_select_value_does_not_decide_rules_visibility():
    out = _run(
        """$("challenge-mode-enabled").value = "true";
syncVisibility();
console.log(JSON.stringify({ rulesHidden: $("rules-block").hidden,
  saveHidden: $("btn-save-race").hidden }));""",
        "{ challenge_mode_enabled: false }",
    )
    assert out == {"rulesHidden": False, "saveHidden": False}


def test_render_challenge_mode_hides_rules_through_real_call_path():
    out = _run(
        """state.race.challenge_mode_enabled = true;
renderChallengeMode();
const on = [$("rules-block").hidden, $("btn-save-race").hidden];
state.race.challenge_mode_enabled = false;
renderChallengeMode();
console.log(JSON.stringify([on, [$("rules-block").hidden, $("btn-save-race").hidden]]));"""
    )
    assert out == [[True, True], [False, False]]


def _note_text(locale: str, duration: int) -> str:
    src = _stripped_script()
    dictionaries_at = src.index("const dictionaries = ")
    dict_end = _match_end(src, src.index("{", dictionaries_at), "{", "}") + 1
    zh_at = src.index('dictionaries["zh-TW"] = ')
    zh_end = _match_end(src, src.index("{", zh_at), "{", "}") + 1
    script = (
        _STUB.replace("const t = (k, p = {}) => k + JSON.stringify(p);\n", "")
        + f"let currentLocale = {json.dumps(locale)};\n"
        + src[dictionaries_at:dict_end]
        + ";\n"
        + src[zh_at:zh_end]
        + ";\n"
        + _extract_function(src, "t")
        + "\n"
        + "\n".join(_extract_function(src, n) for n in _FNS)
        + f"""
state.race = {{ session_mode: "race", state: "IDLE",
  challenge_mode_enabled: true, challenge_duration_sec: {duration} }};
$("competition-mode").value = "individual";
syncVisibility();
console.log(JSON.stringify($("challenge-rules-note-text").textContent));
"""
    )
    return json.loads(_run_node(script))


def test_challenge_note_text_contains_configured_duration_in_english():
    text = _note_text("en-US", 137)
    assert "137" in text
    assert "{seconds}" not in text
    assert "Challenge mode is on" in text


def test_challenge_note_text_contains_configured_duration_in_chinese():
    text = _note_text("zh-TW", 137)
    assert "137" in text
    assert "{seconds}" not in text
    assert "挑戰模式" in text


def test_challenge_note_follows_a_changed_duration():
    assert "90" in _note_text("en-US", 90)
    assert "90" not in _note_text("en-US", 137)


def test_challenge_rules_note_markup_is_a_field_wrapper_around_the_text():
    tree = _tree()
    assert tree.by_id["challenge-rules-note-text"].inside("challenge-rules-note")
