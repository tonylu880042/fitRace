"""Batch 1d: mixed-race "race groups" editing in Game Admin
(hub_server/static/gameAdmin.html).

Covers the two pure functions the spec requires be extracted and executed
for real under node (not pattern-matched on source text):

  * buildRaceGroupsPayload(groups) -- turns the editor's internal group
    shape ({equipmentTypes, raceType, targetValue}) into the wire shape the
    backend RaceGroup model expects ({equipment_types, race_type,
    target_value, duration_sec}), routing the single targetValue number to
    target_value for distance/calories groups and to duration_sec (rounded)
    for time/max_power groups.
  * validateRaceGroups(groups) -- returns an i18n error key (or null) for:
    fewer than 2 groups, a group with no equipment type, an equipment type
    reused across groups, and a group with target/duration <= 0.

Also covers raceGroupsFromConfig(configGroups), the inverse conversion used
to restore the editor from a saved `config.groups` (see renderRace() in the
page), and the EQUIPMENT_TYPES drift guard against
edge_node.domain.models.EQUIPMENT_TYPES.
"""

import json
import re
import subprocess
from pathlib import Path

from edge_node.domain.models import EQUIPMENT_TYPES

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _matching_brace_end(source: str, open_idx: int) -> int:
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


def _matching_paren_end(source: str, open_idx: int) -> int:
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
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing paren found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    paren_open = source.index("(", start)
    paren_close = _matching_paren_end(source, paren_open)
    brace_open = source.index("{", paren_close)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# EQUIPMENT_TYPES drift guard
# ---------------------------------------------------------------------------


def test_equipment_types_constant_matches_edge_domain_minus_unknown():
    source = _stripped_script()
    match = re.search(r"const EQUIPMENT_TYPES\s*=\s*(\[[^\]]*\])", source)
    assert match, "EQUIPMENT_TYPES constant not found in gameAdmin.html"
    values = re.findall(r'"([a-zA-Z_]+)"', match.group(1))
    expected = [t for t in EQUIPMENT_TYPES if t != "unknown"]
    assert values == expected


# ---------------------------------------------------------------------------
# buildRaceGroupsPayload
# ---------------------------------------------------------------------------


def _run_build_payload(groups_js: str):
    source = _stripped_script()
    fn = _extract_function(source, "buildRaceGroupsPayload")
    script = (
        fn + "\n" + f"console.log(JSON.stringify(buildRaceGroupsPayload({groups_js})));"
    )
    return json.loads(_run_node(script))


def test_build_payload_routes_target_value_for_distance_and_calories():
    result = _run_build_payload(
        '[{equipmentTypes: ["treadmill"], raceType: "distance", targetValue: 800}, '
        '{equipmentTypes: ["rowing_machine"], raceType: "calories", targetValue: 250}]'
    )
    assert result[0] == {
        "equipment_types": ["treadmill"],
        "race_type": "distance",
        "target_value": 800,
        "duration_sec": 0,
    }
    assert result[1] == {
        "equipment_types": ["rowing_machine"],
        "race_type": "calories",
        "target_value": 250,
        "duration_sec": 0,
    }


def test_build_payload_routes_and_rounds_duration_for_time_and_max_power():
    result = _run_build_payload(
        '[{equipmentTypes: ["spin_bike"], raceType: "time", targetValue: 90.6}, '
        '{equipmentTypes: ["fan_bike"], raceType: "max_power", targetValue: 30.2}]'
    )
    assert result[0] == {
        "equipment_types": ["spin_bike"],
        "race_type": "time",
        "target_value": 0,
        "duration_sec": 91,
    }
    assert result[1] == {
        "equipment_types": ["fan_bike"],
        "race_type": "max_power",
        "target_value": 0,
        "duration_sec": 30,
    }


def test_build_payload_handles_empty_group_list():
    assert _run_build_payload("[]") == []


# ---------------------------------------------------------------------------
# validateRaceGroups
# ---------------------------------------------------------------------------


def _run_validate(groups_js: str):
    source = _stripped_script()
    fn = _extract_function(source, "validateRaceGroups")
    script = (
        fn + "\n" + f"console.log(JSON.stringify(validateRaceGroups({groups_js})));"
    )
    return json.loads(_run_node(script))


def test_validate_rejects_fewer_than_two_groups():
    result = _run_validate(
        '[{equipmentTypes: ["treadmill"], raceType: "distance", targetValue: 800}]'
    )
    assert result == "message.groups_min"


def test_validate_rejects_a_group_with_no_equipment():
    result = _run_validate(
        '[{equipmentTypes: [], raceType: "distance", targetValue: 800}, '
        '{equipmentTypes: ["rowing_machine"], raceType: "distance", targetValue: 500}]'
    )
    assert result == "message.group_no_equipment"


def test_validate_rejects_equipment_type_in_two_groups():
    result = _run_validate(
        '[{equipmentTypes: ["treadmill"], raceType: "distance", targetValue: 800}, '
        '{equipmentTypes: ["treadmill"], raceType: "distance", targetValue: 500}]'
    )
    assert result == "message.group_equipment_duplicate"


def test_validate_rejects_non_positive_target():
    result = _run_validate(
        '[{equipmentTypes: ["treadmill"], raceType: "distance", targetValue: 0}, '
        '{equipmentTypes: ["rowing_machine"], raceType: "distance", targetValue: 500}]'
    )
    assert result == "message.group_target_required"


def test_validate_accepts_a_well_formed_group_list():
    result = _run_validate(
        '[{equipmentTypes: ["treadmill"], raceType: "distance", targetValue: 800}, '
        '{equipmentTypes: ["rowing_machine"], raceType: "time", targetValue: 300}]'
    )
    assert result is None


# ---------------------------------------------------------------------------
# raceGroupsFromConfig -- inverse conversion, and full load -> save
# round-trip through the two functions above.
# ---------------------------------------------------------------------------


def _run_from_config(config_groups_js: str):
    source = _stripped_script()
    fn = _extract_function(source, "raceGroupsFromConfig")
    script = (
        fn
        + "\n"
        + f"console.log(JSON.stringify(raceGroupsFromConfig({config_groups_js})));"
    )
    return json.loads(_run_node(script))


def test_race_groups_from_config_converts_wire_shape_to_editor_shape():
    result = _run_from_config(
        '[{equipment_types: ["treadmill"], race_type: "distance", target_value: 800, duration_sec: 0}, '
        '{equipment_types: ["rowing_machine"], race_type: "time", target_value: 0, duration_sec: 300}]'
    )
    assert result[0] == {
        "equipmentTypes": ["treadmill"],
        "raceType": "distance",
        "targetValue": 800,
    }
    assert result[1] == {
        "equipmentTypes": ["rowing_machine"],
        "raceType": "time",
        "targetValue": 300,
    }


def test_round_trip_load_then_save_reproduces_the_same_groups():
    saved_groups = [
        {
            "equipment_types": ["treadmill", "curved_treadmill"],
            "race_type": "distance",
            "target_value": 1000,
            "duration_sec": 0,
        },
        {
            "equipment_types": ["rowing_machine"],
            "race_type": "max_power",
            "target_value": 0,
            "duration_sec": 60,
        },
    ]
    source = _stripped_script()
    from_config_fn = _extract_function(source, "raceGroupsFromConfig")
    build_payload_fn = _extract_function(source, "buildRaceGroupsPayload")
    script = (
        from_config_fn
        + "\n"
        + build_payload_fn
        + "\n"
        + f"const loaded = raceGroupsFromConfig({json.dumps(saved_groups)});\n"
        + "console.log(JSON.stringify(buildRaceGroupsPayload(loaded)));"
    )
    result = json.loads(_run_node(script))
    assert result == saved_groups
