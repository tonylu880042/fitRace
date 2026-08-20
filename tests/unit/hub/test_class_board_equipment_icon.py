"""Every station card shows what kind of machine it is.

A coach scanning a 24-station board needs to tell a bike from a treadmill at
a glance, and the machine name alone does not say (Vmax26_35B is a spin bike
only if you already know the model). So each card carries an equipment icon.

Two halves, tested here:

1. The hub puts `equipment_type` on the progress entry, taken from the type
   race_manager remembers per node -- so the icon survives an edge dropping
   out mid-class, unlike a type read from the live edge catalog.
2. The card renders one inline SVG per station, chosen from that type. Inline
   SVG rather than emoji on purpose: Raspberry Pi OS kiosk images routinely
   ship without an emoji font, and a missing glyph renders as a tofu box on
   the projector.
"""

import json
import re
import subprocess
from pathlib import Path

from hub_server.domain.class_models import ClassPlan
from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


# ---------------------------------------------------------------------------
# 1. hub side -- equipment_type reaches the dashboard
# ---------------------------------------------------------------------------


def _telemetry(node_id, equipment_type):
    return {
        "node_id": node_id,
        "equipment_id": "EQ_01",
        "equipment_type": equipment_type,
        "power_watts": 150,
        "instantaneous_speed_kph": 25.0,
        "distance_m": 100.0,
        "elapsed_time_ms": 10_000,
        "timestamp_epoch_ms": 1_700_000_000_000,
    }


def _running_class():
    manager = RaceManager()
    manager.configure_class(ClassPlan(segments=[{"kind": "work", "duration_sec": 600}]))
    manager.start_race()
    return manager


def test_class_progress_entry_carries_the_equipment_type():
    manager = _running_class()
    progress = manager.ingest_telemetry(_telemetry("n1", "treadmill"))
    assert progress["n1"]["equipment_type"] == "treadmill"


def test_race_progress_entry_carries_the_equipment_type_too():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.start_race()
    progress = manager.ingest_telemetry(_telemetry("n1", "rower"))
    assert progress["n1"]["equipment_type"] == "rower"


def test_equipment_type_survives_a_sample_that_omits_it():
    """The antenna reports the type on the samples that carry it; a later
    sample without one must not blank the icon."""
    manager = _running_class()
    manager.ingest_telemetry(_telemetry("n1", "ski_erg"))

    without_type = _telemetry("n1", "ski_erg")
    del without_type["equipment_type"]
    progress = manager.ingest_telemetry(without_type)

    assert progress["n1"]["equipment_type"] == "ski_erg"


# ---------------------------------------------------------------------------
# 2. card side -- one icon per station, chosen from the type
# ---------------------------------------------------------------------------


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    return source[start : _matching_brace_end(source, brace_open) + 1]


def _stubs() -> str:
    return (
        "const t = (key, params = {}) => { let value = `T[${key}]`; "
        "Object.entries(params).forEach(([name, replacement]) => { "
        "value = value.replaceAll(`{${name}}`, String(replacement)); }); "
        "return value; };\n"
        "const metricNumber = (value, fallback = 0) => { const n = Number(value); "
        "return Number.isFinite(n) ? n : fallback; };\n"
        "const escapeHtml = (value) => String(value ?? '')"
        ".replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')"
        ".replace(/\"/g, '&quot;').replace(/'/g, '&#039;');\n"
        "const nodeDisplayName = (node) => "
        "node?.node_display_name || node?.display_name || node?.node_id || '--';\n"
        "const Intl = { NumberFormat: function() { return { format: (n) => String(n) }; } };\n"
        "const formatClock = (ms) => {\n"
        "  const total = Math.max(0, Math.floor(ms / 1000));\n"
        "  const m = String(Math.floor(total / 60)).padStart(2, '0');\n"
        "  const s = String(total % 60).padStart(2, '0');\n"
        "  return `${m}:${s}`;\n"
        "};\n"
        "const currentLocale = 'en-US';\n"
    )


def _board_html(types):
    """types: list of equipment_type values, one station each."""
    leaderboard = {
        f"n{i}": {
            "node_id": f"n{i}",
            "station_number": i + 1,
            "athlete_name": f"Machine {i + 1}",
            "equipment_type": equipment_type,
            "power_watts": 180,
            "instantaneous_speed_kph": 25,
            "distance_m": 500,
        }
        for i, equipment_type in enumerate(types)
    }
    source = _read_index()
    script = (
        _stubs()
        + _extract_function(source, "buildClassBoardHtml")
        + "\nconst sessionData = "
        + json.dumps(
            {"class_plan": {"segments": [{"kind": "work", "duration_sec": 600}]}}
        )[:-1]
        + ', "leaderboard": '
        + json.dumps(leaderboard)
        + "};\n"
        + "const clock = {index: 0, kind: 'work', segmentRemainingMs: 600000, "
        "totalRemainingMs: 600000, finished: false};\n"
        + "console.log(buildClassBoardHtml(sessionData, clock));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}")
    return result.stdout


def _icons(html):
    return re.findall(r'data-equipment-icon="([^"]+)"', html)


def test_each_equipment_family_gets_its_own_icon():
    html = _board_html(
        ["spin_bike", "treadmill", "rower", "ski_erg"],
    )
    assert _icons(html) == ["bike", "treadmill", "rower", "ski"]


def test_every_bike_flavour_shares_the_bike_icon():
    html = _board_html(["fan_bike", "indoor_bike", "spin_bike"])
    assert _icons(html) == ["bike", "bike", "bike"]


def test_rowing_machine_is_the_same_family_as_rower():
    html = _board_html(["rowing_machine"])
    assert _icons(html) == ["rower"]


def test_an_unknown_or_missing_type_still_gets_an_icon():
    """A station with no type yet must not render a hole where the other
    cards have an icon."""
    html = _board_html(["treadmill_2000_pro", None])
    assert _icons(html) == ["generic", "generic"]


def test_one_icon_per_station_card():
    html = _board_html(["spin_bike"] * 24)
    assert len(_icons(html)) == 24
    assert html.count("<svg") == 24


def test_the_icon_is_decorative_for_assistive_tech():
    """The card already names the machine in text; the icon repeats it."""
    html = _board_html(["spin_bike"])
    icon = re.search(r"<svg[^>]*>", html).group(0)
    assert 'aria-hidden="true"' in icon
