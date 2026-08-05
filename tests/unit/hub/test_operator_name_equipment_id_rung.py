"""The operator-facing node name is composed on the hub as
`Node{IP octet}+{BLE name}` and shipped down as `node_display_name` /
`unassigned_node_display_names`. That is already correct almost everywhere
it is set. But two render sites in gameAdmin.html and one in index.html
used to fall from that resolved name straight to the internal plumbing id
(`fitrace-edge-01-01`), skipping the BLE name (`equipment_id`) that the hub
now also ships alongside it. This pins the exact three-rung fallback chain
`display_name -> equipment_id -> node_id` at each of those render sites, so
an edit that drops the equipment_id rung again is caught -- against
*comment-stripped* source, so a comment mentioning the same text can't
satisfy the assertion while the real expression is reverted.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# Comment-stripping helpers (CLAUDE.md: a comment must not satisfy a source
# assertion). Mirrors tests/unit/hub/test_dashboard_local_qr.py.
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _stripped_script(filename: str) -> str:
    source = (STATIC_DIR / filename).read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def test_game_admin_unassigned_row_prefers_equipment_id_over_raw_node_id():
    script = _stripped_script("gameAdmin.html")
    assert (
        "state.stations.unassigned_node_display_names?.[nodeId] || "
        "state.stations.unassigned_node_equipment_ids?.[nodeId] || nodeId"
    ) in script


def test_game_admin_assigned_row_prefers_equipment_id_over_raw_node_id():
    script = _stripped_script("gameAdmin.html")
    assert (
        "station.node_display_name || station.equipment_id || station.node_id"
    ) in script


def test_dashboard_station_label_prefers_equipment_id_over_raw_node_id():
    script = _stripped_script("index.html")
    assert ("info.node_display_name || info.equipment_id || info.node_id") in script
