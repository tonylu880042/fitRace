"""Guard against drift between the two copies of the upcoming-segment
announcement decision in `hub_server/static/index.html`:

  - `computeUpcomingSegmentAnnouncement`, nested inside `buildClassBoardHtml`
  - `computeUpcomingSegmentAnnouncementForPatch`, a top-level twin used by
    the incremental patch path (`applyClassBoardIncrementalUpdate`)

These two are identical logic (see both functions' declaration comments in
index.html), including a `const ANNOUNCEMENT_LEAD_MS = 10000;` declared
separately inside EACH. That duplication was investigated for this refactor
and found unavoidable given the existing test suite's extraction technique:

  - tests/unit/hub/test_dashboard_class_next_segment_announcement.py section
    2 (`_run_build_class_board_html`) extracts `buildClassBoardHtml` ALONE --
    by its `function buildClassBoardHtml(` marker, brace-matched -- and runs
    it under `node -e` with only a handful of primitive stubs (t,
    metricNumber, escapeHtml, nodeDisplayName, Intl, formatClock). It does
    NOT extract any sibling top-level function. If the nested
    `computeUpcomingSegmentAnnouncement` were replaced with a call out to
    the top-level `computeUpcomingSegmentAnnouncementForPatch` (or removed
    in favour of it), every one of that test module's buildClassBoardHtml
    render-path tests would ReferenceError, because
    `computeUpcomingSegmentAnnouncementForPatch` is never defined in that
    narrow extraction. Those are pre-existing passing tests this refactor
    must not break (CLAUDE.md: never edit a passing test to make a change
    fit).
  - Symmetrically, `applyClassBoardIncrementalUpdate` (the incremental patch
    path) is extracted as a TOP-LEVEL sibling alongside
    `computeUpcomingSegmentAnnouncementForPatch` in
    tests/unit/hub/test_dashboard_class_board_card_cache.py and
    test_dashboard_class_next_segment_announcement.py section 3 -- it is
    never extracted bundled inside buildClassBoardHtml, so it cannot reach
    a copy nested inside that function either.

So a single shared definition cannot serve both call sites without breaking
the existing narrow-extraction tests. Per CLAUDE.md's guidance for exactly
this situation, both copies are kept, and this module is the guard: it
extracts BOTH functions independently and asserts they produce identical
output across a shared table of inputs, including the threshold boundary at
exactly 10000ms. If a future edit changes one ANNOUNCEMENT_LEAD_MS (or any
other part of the decision) without the other, this test catches the drift
immediately instead of leaving a live discrepancy between the initial render
and the per-second incremental patch.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


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
    # An exact "function NAME(" marker -- the trailing "(" means this never
    # matches a longer name that merely starts with NAME (e.g. extracting
    # "computeUpcomingSegmentAnnouncement" alone never matches inside
    # "computeUpcomingSegmentAnnouncementForPatch(", since the character
    # right after "Announcement" there is "F", not "(").
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _run_nested_decision(clock_js: str, plan_js: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(
        _extract_function(source, "computeUpcomingSegmentAnnouncement")
    )
    script = (
        fn
        + "\n"
        + f"const clock = {clock_js};\n"
        + f"const plan = {plan_js};\n"
        + "console.log(JSON.stringify(computeUpcomingSegmentAnnouncement(clock, plan)));"
    )
    return json.loads(_run_node(script))


def _run_patch_decision(clock_js: str, plan_js: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(
        _extract_function(source, "computeUpcomingSegmentAnnouncementForPatch")
    )
    script = (
        fn
        + "\n"
        + f"const clock = {clock_js};\n"
        + f"const plan = {plan_js};\n"
        + "console.log(JSON.stringify(computeUpcomingSegmentAnnouncementForPatch(clock, plan)));"
    )
    return json.loads(_run_node(script))


_TWO_SEGMENT_PLAN_WITH_TARGET = json.dumps(
    {
        "segments": [
            {"kind": "work", "duration_sec": 300},
            {"kind": "rest", "duration_sec": 60, "target_watts": 150},
        ]
    }
)

_TWO_SEGMENT_PLAN_NO_TARGET = json.dumps(
    {
        "segments": [
            {"kind": "work", "duration_sec": 300},
            {"kind": "cooldown", "duration_sec": 60},
        ]
    }
)

_THREE_SEGMENT_PLAN = json.dumps(
    {
        "segments": [
            {"kind": "warmup", "duration_sec": 30},
            {"kind": "work", "duration_sec": 300},
            {"kind": "cooldown", "duration_sec": 60},
        ]
    }
)

_SINGLE_SEGMENT_PLAN = json.dumps({"segments": [{"kind": "work", "duration_sec": 300}]})


def _clock(index, segment_remaining_ms, finished=False):
    return json.dumps(
        {
            "index": index,
            "kind": "work",
            "segmentRemainingMs": segment_remaining_ms,
            "totalRemainingMs": segment_remaining_ms + 60000,
            "finished": finished,
        }
    )


# (description, clock_js, plan_js) -- a shared table of inputs run through
# BOTH implementations. Includes the exact 10000ms threshold boundary on
# both sides, since that boundary is precisely where a diverged
# ANNOUNCEMENT_LEAD_MS constant would first show up as a disagreement.
_CASES = [
    ("hidden at 10001ms remaining", _clock(0, 10001), _TWO_SEGMENT_PLAN_WITH_TARGET),
    (
        "shown at exactly 10000ms remaining (the boundary)",
        _clock(0, 10000),
        _TWO_SEGMENT_PLAN_WITH_TARGET,
    ),
    ("shown at 9999ms remaining", _clock(0, 9999), _TWO_SEGMENT_PLAN_WITH_TARGET),
    ("hidden at 11000ms remaining", _clock(0, 11000), _TWO_SEGMENT_PLAN_WITH_TARGET),
    ("shown at 1ms remaining", _clock(0, 1), _TWO_SEGMENT_PLAN_WITH_TARGET),
    (
        "shown without watts when next segment has no target",
        _clock(0, 5000),
        _TWO_SEGMENT_PLAN_NO_TARGET,
    ),
    (
        "never shown on the last segment of the plan",
        _clock(1, 3000),
        _TWO_SEGMENT_PLAN_WITH_TARGET,
    ),
    (
        "never shown once the plan has finished",
        _clock(0, 0, finished=True),
        _THREE_SEGMENT_PLAN,
    ),
    (
        "never shown on a single-segment plan (no next segment at all)",
        _clock(0, 500),
        _SINGLE_SEGMENT_PLAN,
    ),
]


def test_nested_and_top_level_announcement_decisions_agree_on_every_case():
    mismatches = []
    for description, clock_js, plan_js in _CASES:
        nested_result = _run_nested_decision(clock_js, plan_js)
        patch_result = _run_patch_decision(clock_js, plan_js)
        if nested_result != patch_result:
            mismatches.append((description, nested_result, patch_result))
    assert not mismatches, (
        "computeUpcomingSegmentAnnouncement (nested) and "
        "computeUpcomingSegmentAnnouncementForPatch (top-level) disagree:\n"
        + "\n".join(
            f"  {desc}: nested={nested!r} vs forPatch={patch!r}"
            for desc, nested, patch in mismatches
        )
    )


def test_exact_boundary_case_individually_pinned():
    # Isolated, explicit pin of the load-bearing boundary case: at exactly
    # ANNOUNCEMENT_LEAD_MS remaining the announcement must be SHOWN on both
    # paths -- this is the exact value that would drift first if only one
    # of the two duplicated constants were ever changed.
    clock_js = _clock(0, 10000)
    nested_result = _run_nested_decision(clock_js, _TWO_SEGMENT_PLAN_WITH_TARGET)
    patch_result = _run_patch_decision(clock_js, _TWO_SEGMENT_PLAN_WITH_TARGET)
    assert nested_result == {"kind": "rest", "targetWatts": 150}
    assert patch_result == {"kind": "rest", "targetWatts": 150}
    assert nested_result == patch_result
