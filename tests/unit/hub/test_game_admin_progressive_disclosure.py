"""Progressive disclosure for Game Admin (docs/game_admin_progressive_disclosure_spec.md).

A setting appears only when an earlier choice makes it relevant. Hidden is not
disabled: irrelevant fields are collapsed (`field-collapsed` / `hidden`), and
hiding never changes a save payload.

Pure logic (`disclosureState`) is extracted from the comment-stripped inline
script and executed under `node -e`; DOM structure is checked by parsing the
HTML (HTMLParser ignores comments), never by substring grep.
"""

import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", code))


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
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


# ---------------------------------------------------------------------------
# HTML structure helper
# ---------------------------------------------------------------------------

_VOID = {"input", "br", "img", "meta", "link", "hr"}


class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def inside(self, tag=None, id_=None):
        return any(
            (tag is None or a.tag == tag) and (id_ is None or a.attrs.get("id") == id_)
            for a in self.ancestors()
        )


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = _Node("root", [], None)
        self.cur = self.root
        self.by_id = {}

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
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
    parser.feed(_read())
    return parser


# ---------------------------------------------------------------------------
# disclosureState -- one pure partition
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


def test_individual_hides_relay_team_fields_and_team_rule_card():
    s = _disclosure(competitionMode="individual")
    assert s["relayLegs"] is False
    assert s["teamScoring"] is False
    assert s["teamCompletion"] is False
    assert s["teamRuleCard"] is False


def test_team_shows_team_fields_and_card_but_not_relay_legs():
    s = _disclosure(competitionMode="team")
    assert s["relayLegs"] is False
    assert s["teamScoring"] is True
    assert s["teamCompletion"] is True
    assert s["teamRuleCard"] is True


def test_relay_shows_legs_and_card_but_not_team_fields():
    s = _disclosure(competitionMode="relay")
    assert s["relayLegs"] is True
    assert s["teamScoring"] is False
    assert s["teamCompletion"] is False
    assert s["teamRuleCard"] is True


def test_challenge_settings_follow_challenge_mode():
    assert _disclosure(challengeModeOn=True)["challengeSettings"] is True
    assert _disclosure(challengeModeOn=False)["challengeSettings"] is False


def test_switch_to_race_mode_visible_unless_session_is_race():
    assert _disclosure(sessionMode="race")["switchToRaceMode"] is False
    assert _disclosure(sessionMode="class")["switchToRaceMode"] is True
    assert _disclosure(sessionMode=None)["switchToRaceMode"] is True


def test_roster_lists_follow_has_roster():
    assert _disclosure(hasRoster=True)["rosterLists"] is True
    assert _disclosure(hasRoster=False)["rosterLists"] is False


# ---------------------------------------------------------------------------
# syncVisibility -- applies the decision to the DOM (R1, R2, R6)
# ---------------------------------------------------------------------------

_STUB = """
const mk = () => ({
  classList: {
    s: new Set(),
    toggle(c, f) { if (f) this.s.add(c); else this.s.delete(c); },
    contains(c) { return this.s.has(c); },
  },
  hidden: false,
  value: "",
});
const els = {};
const $ = (id) => (els[id] = els[id] || mk());
const state = { race: { session_mode: "race", state: "IDLE" },
                roster: { counts: { pending: 0, loaded: 0, done: 0, absent: 0 } } };
"""

_FIELDS = ["relay-legs-field", "team-scoring-field", "team-completion-field"]


def _run_sync(competition_mode: str) -> dict:
    src = _stripped_script()
    fns = "\n".join(
        _extract_function(src, n) for n in ("disclosureState", "syncVisibility")
    )
    script = _STUB + fns + f"""
$("competition-mode").value = {json.dumps(competition_mode)};
syncVisibility();
console.log(JSON.stringify({{
  collapsed: {json.dumps(_FIELDS)}.map((id) => $(id).classList.contains("field-collapsed")),
  ruleCardHidden: $("team-rule-card").hidden,
}}));
"""
    return json.loads(_run_node(script))


def test_sync_visibility_individual_collapses_all_three_fields_and_card():
    out = _run_sync("individual")
    assert out["collapsed"] == [True, True, True]
    assert out["ruleCardHidden"] is True


def test_sync_visibility_team_shows_team_fields_only():
    out = _run_sync("team")
    assert out["collapsed"] == [True, False, False]
    assert out["ruleCardHidden"] is False


def test_sync_visibility_relay_shows_legs_only():
    out = _run_sync("relay")
    assert out["collapsed"] == [False, True, True]
    assert out["ruleCardHidden"] is False


def test_sync_competition_fields_no_longer_greys_out_irrelevant_fields():
    body = _extract_function(_stripped_script(), "syncCompetitionFields")
    assert '"relay-legs-field"' not in body
    assert '"team-scoring-field"' not in body
    assert '"team-scoring-note"' not in body


def test_relay_and_team_notes_removed_from_markup():
    tree = _tree()
    assert "relay-legs-note" not in tree.by_id
    assert "team-scoring-note" not in tree.by_id
    assert "team-completion-note" in tree.by_id  # time-based constraint note stays


def test_sync_visibility_called_from_every_competition_sync_site():
    tree = _tree()
    onchange = tree.by_id["competition-mode"].attrs["onchange"]
    assert "syncVisibility()" in onchange
    src = _stripped_script()
    assert "syncVisibility()" in _extract_function(src, "syncSessionModeControl")
    init_tail = src[src.rindex("syncMixedRaceFields();") :]
    assert "syncVisibility()" in init_tail


def test_team_rule_card_is_a_guidance_card_in_markup():
    tree = _tree()
    card = tree.by_id["team-rule-card"]
    assert "guidance-card" in card.classes
    assert "team-rule-summary" in tree.by_id


# ---------------------------------------------------------------------------
# Hiding never touches values or the save payload
# ---------------------------------------------------------------------------


def _configure_payload(toggle: bool) -> dict:
    src = _stripped_script()
    fns = "\n".join(
        [
            _extract_function(src, "disclosureState"),
            _extract_function(src, "syncVisibility"),
            _extract_function(src, "isRelayCompetitionMode"),
            "async " + _extract_function(src, "configureRace"),
        ]
    )
    script = (
        _STUB
        + """
const posts = [];
const adminHeaders = (h) => h;
const setMessage = () => {};
const t = (k) => k;
const refreshReadiness = async () => {};
const renderRace = () => {};
const validateRaceGroups = () => null;
const buildRaceGroupsPayload = () => [];
async function fetchJson(url, opts) { posts.push(JSON.parse(opts.body)); return {}; }
"""
        + fns
        + f"""
$("race-type").value = "distance";
$("competition-mode").value = "team";
$("team-scoring-policy").value = "total";
$("team-completion-policy").value = "all_members";
$("relay-legs").value = "4";
$("race-target").value = "500";
syncVisibility();
if ({json.dumps(toggle)}) {{
  $("competition-mode").value = "individual"; syncVisibility();
  $("competition-mode").value = "team"; syncVisibility();
}}
configureRace().then(() => console.log(JSON.stringify({{
  body: posts[0],
  scoring: $("team-scoring-policy").value,
  completion: $("team-completion-policy").value,
}})));
"""
    )
    return json.loads(_run_node(script))


def test_toggling_competition_keeps_team_values_and_payload_unchanged():
    baseline = _configure_payload(toggle=False)
    toggled = _configure_payload(toggle=True)
    assert toggled == baseline
    assert toggled["scoring"] == "total"
    assert toggled["completion"] == "all_members"
    assert baseline["body"]["team_scoring_policy"] == "total"
    assert baseline["body"]["team_completion_policy"] == "all_members"


# ---------------------------------------------------------------------------
# R4 -- Switch Projector to Race Mode only while the hub is not in race mode
# ---------------------------------------------------------------------------


def _run_sync_with_state(state_js: str, extra: str = "") -> dict:
    src = _stripped_script()
    fns = "\n".join(
        _extract_function(src, n) for n in ("disclosureState", "syncVisibility")
    )
    script = _STUB + fns + f"""
Object.assign(state, {state_js});
$("competition-mode").value = "individual";
syncVisibility();
{extra}
console.log(JSON.stringify({{
  switchCollapsed: $("switch-to-race-mode-field").classList.contains("field-collapsed"),
}}));
"""
    return json.loads(_run_node(script))


def test_switch_to_race_mode_field_is_collapsed_when_session_mode_is_race():
    out = _run_sync_with_state('{ race: { session_mode: "race", state: "IDLE" } }')
    assert out["switchCollapsed"] is True


def test_switch_to_race_mode_field_is_shown_when_session_mode_is_class():
    out = _run_sync_with_state('{ race: { session_mode: "class", state: "IDLE" } }')
    assert out["switchCollapsed"] is False


def test_switch_to_race_mode_field_markup_wraps_button_and_note():
    tree = _tree()
    field = tree.by_id["switch-to-race-mode-field"]
    assert "field" in field.classes
    assert tree.by_id["btn-switch-to-race-mode"].inside(id_="switch-to-race-mode-field")
    assert tree.by_id["switch-to-race-mode-note"].inside(
        id_="switch-to-race-mode-field"
    )


# ---------------------------------------------------------------------------
# R5 -- heat/queue blocks appear only once the roster has entries
# ---------------------------------------------------------------------------

_ROSTER_BLOCKS = [
    "roster-current-heat-block",
    "roster-next-heat-block",
    "roster-heat-actions",
    "roster-pending-block",
    "roster-done-block",
    "roster-absent-block",
]


def _run_roster_visibility(counts_js: str) -> list:
    src = _stripped_script()
    fns = "\n".join(
        _extract_function(src, n) for n in ("disclosureState", "syncVisibility")
    )
    script = _STUB + fns + f"""
state.roster = {{ counts: {counts_js} }};
$("competition-mode").value = "individual";
syncVisibility();
console.log(JSON.stringify({json.dumps(_ROSTER_BLOCKS)}.map((id) => $(id).hidden)));
"""
    return json.loads(_run_node(script))


def test_empty_roster_hides_every_heat_and_queue_block():
    hidden = _run_roster_visibility("{ pending: 0, loaded: 0, done: 0, absent: 0 }")
    assert hidden == [True] * len(_ROSTER_BLOCKS)


def test_any_roster_entry_shows_every_heat_and_queue_block():
    for counts in (
        "{ pending: 1, loaded: 0, done: 0, absent: 0 }",
        "{ pending: 0, loaded: 1, done: 0, absent: 0 }",
        "{ pending: 0, loaded: 0, done: 1, absent: 0 }",
        "{ pending: 0, loaded: 0, done: 0, absent: 1 }",
    ):
        assert _run_roster_visibility(counts) == [False] * len(_ROSTER_BLOCKS), counts


def test_roster_blocks_exist_in_roster_panel_and_walk_in_and_toolbar_do_not_hide():
    tree = _tree()
    for block_id in _ROSTER_BLOCKS:
        assert tree.by_id[block_id].inside(tag="section"), block_id
    assert tree.by_id["roster-current-heat"].inside(id_="roster-current-heat-block")
    assert tree.by_id["roster-next-heat"].inside(id_="roster-next-heat-block")
    assert tree.by_id["btn-load-next-heat"].inside(id_="roster-heat-actions")
    assert tree.by_id["roster-pending-list"].inside(id_="roster-pending-block")
    assert tree.by_id["roster-done-list"].inside(id_="roster-done-block")
    assert tree.by_id["roster-absent-list"].inside(id_="roster-absent-block")
    # Always-visible: walk-in registration, import toolbar, roster status.
    for always in ("walk-in-name", "roster-file-input", "roster-counts"):
        node = tree.by_id[always]
        for block_id in _ROSTER_BLOCKS:
            assert not node.inside(id_=block_id), (always, block_id)


def test_render_roster_re_syncs_visibility_after_every_roster_change():
    assert "syncVisibility()" in _extract_function(_stripped_script(), "renderRoster")
