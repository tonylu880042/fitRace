"""Tests for the durable, named library of class plans.

A class plan used to be treated as session state: `reset_race()` deleted it
outright. Tony (the coach) considers a class plan VENUE CONFIGURATION --
like station assignment -- so it must only disappear when the operator
deletes it explicitly. This module covers:

1. RaceManager's named library (`list_class_plans`/`save_class_plan`/
   `delete_class_plan`), stored under settings key "class_plans" alongside
   the existing keys, loaded defensively (an old settings file with the key
   absent, or one bad entry, must not break startup).
2. The admin API surface: GET/POST/DELETE /api/class/plans.

The already-inverted reset-keeps-the-plan tests live in
tests/unit/hub/test_class_session.py (that file's existing coverage of
reset_race()); this file is only the new library surface.
"""

import pytest
from fastapi.testclient import TestClient

from hub_server.domain.class_models import ClassPlan
from hub_server.infrastructure.fastapi import app as app_module
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_settings_store import RaceSettingsStore

client = TestClient(app_module.app)


def _plan(*durations):
    return ClassPlan(segments=[{"kind": "work", "duration_sec": d} for d in durations])


# ---------------------------------------------------------------------------
# 1. list_class_plans -- empty by default, returns a defensive copy.
# ---------------------------------------------------------------------------


def test_list_class_plans_empty_by_default():
    manager = RaceManager()
    assert manager.list_class_plans() == {}


def test_list_class_plans_returns_a_copy_not_the_live_dict():
    manager = RaceManager()
    original = _plan(60, 60)
    manager.save_class_plan("Leg Day", original)
    snapshot = manager.list_class_plans()
    snapshot["Leg Day"] = None
    snapshot["Intruder"] = _plan(5)
    # Mutating the returned dict must not leak back into the manager.
    fresh = manager.list_class_plans()
    assert list(fresh.keys()) == ["Leg Day"]
    assert fresh["Leg Day"] is original


# ---------------------------------------------------------------------------
# 2. save_class_plan -- strip, validate length, upsert, persist.
# ---------------------------------------------------------------------------


def test_save_class_plan_stores_under_the_given_name():
    manager = RaceManager()
    plan = _plan(300, 1200, 300)
    manager.save_class_plan("Spin 45", plan)
    assert manager.list_class_plans() == {"Spin 45": plan}


def test_save_class_plan_strips_the_name():
    manager = RaceManager()
    plan = _plan(60)
    manager.save_class_plan("  Leg Day  ", plan)
    assert list(manager.list_class_plans().keys()) == ["Leg Day"]


def test_save_class_plan_rejects_empty_name_after_strip():
    manager = RaceManager()
    with pytest.raises(ValueError):
        manager.save_class_plan("   ", _plan(60))
    assert manager.list_class_plans() == {}


def test_save_class_plan_rejects_empty_string_name():
    manager = RaceManager()
    with pytest.raises(ValueError):
        manager.save_class_plan("", _plan(60))


def test_save_class_plan_rejects_name_longer_than_60_chars():
    manager = RaceManager()
    with pytest.raises(ValueError):
        manager.save_class_plan("x" * 61, _plan(60))
    assert manager.list_class_plans() == {}


def test_save_class_plan_accepts_name_exactly_60_chars():
    manager = RaceManager()
    name = "x" * 60
    manager.save_class_plan(name, _plan(60))
    assert name in manager.list_class_plans()


def test_save_class_plan_same_name_overwrites_upsert():
    manager = RaceManager()
    manager.save_class_plan("Leg Day", _plan(60))
    replacement = _plan(90, 90)
    manager.save_class_plan("Leg Day", replacement)
    plans = manager.list_class_plans()
    assert len(plans) == 1
    assert plans["Leg Day"] is replacement


def test_save_class_plan_persists_to_disk(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    manager = RaceManager(settings_store=store)
    manager.save_class_plan("Spin 45", _plan(300, 1200, 300))

    restarted = RaceManager(
        settings_store=RaceSettingsStore(tmp_path / "settings.json")
    )
    plans = restarted.list_class_plans()
    assert list(plans.keys()) == ["Spin 45"]
    assert plans["Spin 45"].total_duration_sec == 1800


# ---------------------------------------------------------------------------
# 3. delete_class_plan -- True/False, persists only when something removed.
# ---------------------------------------------------------------------------


def test_delete_class_plan_removes_and_returns_true():
    manager = RaceManager()
    manager.save_class_plan("Leg Day", _plan(60))
    assert manager.delete_class_plan("Leg Day") is True
    assert manager.list_class_plans() == {}


def test_delete_class_plan_missing_name_returns_false():
    manager = RaceManager()
    assert manager.delete_class_plan("Nonexistent") is False


def test_delete_class_plan_strips_the_name_like_save_does():
    """save_class_plan stores under the stripped name, so a delete of the
    same operator-typed string (a trailing space survives a URL path, an
    input field, or a copy-paste) has to find it."""
    manager = RaceManager()
    manager.save_class_plan("  Leg Day  ", _plan(60))
    assert manager.delete_class_plan("  Leg Day  ") is True
    assert manager.list_class_plans() == {}


def test_delete_class_plan_still_reports_false_for_a_name_that_is_only_space():
    """Stripping must not turn a junk name into a hit on some other entry."""
    manager = RaceManager()
    manager.save_class_plan("Leg Day", _plan(60))
    assert manager.delete_class_plan("   ") is False
    assert list(manager.list_class_plans()) == ["Leg Day"]


def test_delete_class_plan_persists_the_removal(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    manager = RaceManager(settings_store=store)
    manager.save_class_plan("Leg Day", _plan(60))
    manager.delete_class_plan("Leg Day")

    restarted = RaceManager(
        settings_store=RaceSettingsStore(tmp_path / "settings.json")
    )
    assert restarted.list_class_plans() == {}


def test_delete_class_plan_on_missing_name_does_not_touch_disk(tmp_path):
    # A no-op delete must not rewrite (and potentially corrupt/race) the
    # settings file -- _persist_settings() is only called when something
    # was actually removed.
    settings_path = tmp_path / "settings.json"
    store = RaceSettingsStore(settings_path)
    manager = RaceManager(settings_store=store)
    manager.save_class_plan("Leg Day", _plan(60))
    before = settings_path.read_text(encoding="utf-8")

    assert manager.delete_class_plan("Nonexistent") is False

    after = settings_path.read_text(encoding="utf-8")
    assert after == before


# ---------------------------------------------------------------------------
# 4. reset_race() does not touch the library (only the currently-active
# plan's own survival is covered by test_class_session.py).
# ---------------------------------------------------------------------------


def test_reset_race_does_not_clear_the_saved_plan_library():
    manager = RaceManager()
    manager.save_class_plan("Leg Day", _plan(60, 60))
    manager.configure_class(_plan(10, 10))
    manager.start_race()
    manager.reset_race()
    assert "Leg Day" in manager.list_class_plans()


def test_reset_race_does_not_clear_the_saved_plan_library_on_disk(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    manager = RaceManager(settings_store=store)
    manager.save_class_plan("Leg Day", _plan(60, 60))
    manager.reset_race()

    restarted = RaceManager(
        settings_store=RaceSettingsStore(tmp_path / "settings.json")
    )
    assert "Leg Day" in restarted.list_class_plans()


# ---------------------------------------------------------------------------
# 5. Defensive loading: an old settings.json (key absent) loads as an empty
# library; a malformed "class_plans" value, or one bad entry inside it,
# is skipped rather than fatal.
# ---------------------------------------------------------------------------


def test_old_settings_file_without_class_plans_key_loads_as_empty_library(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    store.save(
        {
            "stations": {},
            "leaderboard_display_mode": "classic",
            "start_countdown_sound_enabled": True,
            "config": None,
            "session_mode": "race",
            "class_plan": None,
        }
    )
    manager = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    assert manager.list_class_plans() == {}


def test_class_plans_value_of_wrong_type_loads_as_empty_library(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    store.save(
        {
            "stations": {},
            "leaderboard_display_mode": "classic",
            "start_countdown_sound_enabled": True,
            "config": None,
            "class_plans": "not-a-dict",
        }
    )
    manager = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    assert manager.list_class_plans() == {}


def test_one_malformed_entry_is_skipped_not_fatal(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    store.save(
        {
            "stations": {},
            "leaderboard_display_mode": "classic",
            "start_countdown_sound_enabled": True,
            "config": None,
            "class_plans": {
                "Good Plan": {"segments": [{"kind": "work", "duration_sec": 60}]},
                "Bad Plan": {"segments": "not-a-list"},
                "Also Bad": "not-even-a-dict",
            },
        }
    )
    manager = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    plans = manager.list_class_plans()
    assert list(plans.keys()) == ["Good Plan"]
    assert plans["Good Plan"].total_duration_sec == 60


def test_class_plans_round_trip_multiple_entries(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    manager = RaceManager(settings_store=store)
    manager.save_class_plan("Spin 45", _plan(300, 1200, 300))
    manager.save_class_plan("Leg Day", _plan(60, 60))

    restarted = RaceManager(
        settings_store=RaceSettingsStore(tmp_path / "settings.json")
    )
    plans = restarted.list_class_plans()
    assert set(plans.keys()) == {"Spin 45", "Leg Day"}
    assert plans["Spin 45"].total_duration_sec == 1800
    assert plans["Leg Day"].total_duration_sec == 120


# ---------------------------------------------------------------------------
# 6. Admin API: GET/POST/DELETE /api/class/plans.
#
# The shared module-level race_manager is process-wide (see
# tests/integration/test_api.py's isolation fix in the previous commit), so
# every test here saves/deletes only names it created, and an autouse
# fixture wipes the whole library before AND after each test -- belt and
# braces against leaking a saved name into an unrelated test elsewhere in
# the suite.
# ---------------------------------------------------------------------------

_SEGMENTS_A = {"segments": [{"kind": "work", "duration_sec": 60}]}
_SEGMENTS_B = {
    "segments": [
        {"kind": "warmup", "duration_sec": 90},
        {"kind": "work", "duration_sec": 300, "target_watts": 150},
    ]
}


@pytest.fixture(autouse=True)
def _clean_class_plan_library():
    def _wipe():
        for name in list(app_module.race_manager.list_class_plans().keys()):
            app_module.race_manager.delete_class_plan(name)

    _wipe()
    yield
    _wipe()


def test_get_class_plans_empty_by_default():
    res = client.get("/api/class/plans")
    assert res.status_code == 200
    assert res.json() == {"plans": []}


def test_post_class_plans_saves_and_returns_the_list():
    res = client.post("/api/class/plans", json={"name": "Spin 45", "plan": _SEGMENTS_A})
    assert res.status_code == 200
    body = res.json()
    assert body["plans"] == [{"name": "Spin 45", "plan": _expected_plan(_SEGMENTS_A)}]


def test_get_class_plans_reflects_a_previous_save():
    client.post("/api/class/plans", json={"name": "Spin 45", "plan": _SEGMENTS_A})
    res = client.get("/api/class/plans")
    assert res.status_code == 200
    assert [entry["name"] for entry in res.json()["plans"]] == ["Spin 45"]


def test_post_class_plans_upserts_same_name():
    client.post("/api/class/plans", json={"name": "Spin 45", "plan": _SEGMENTS_A})
    res = client.post("/api/class/plans", json={"name": "Spin 45", "plan": _SEGMENTS_B})
    assert res.status_code == 200
    body = res.json()
    assert len(body["plans"]) == 1
    assert body["plans"][0]["plan"] == _expected_plan(_SEGMENTS_B)


def test_post_class_plans_response_sorted_by_name():
    client.post("/api/class/plans", json={"name": "Zumba", "plan": _SEGMENTS_A})
    res = client.post(
        "/api/class/plans", json={"name": "Ab Blast", "plan": _SEGMENTS_A}
    )
    assert res.status_code == 200
    names = [entry["name"] for entry in res.json()["plans"]]
    assert names == ["Ab Blast", "Zumba"]


def test_post_class_plans_whitespace_only_name_is_400_not_500():
    res = client.post("/api/class/plans", json={"name": "   ", "plan": _SEGMENTS_A})
    assert res.status_code == 400
    assert client.get("/api/class/plans").json() == {"plans": []}


def test_post_class_plans_empty_string_name_is_422_from_pydantic_min_length():
    # min_length=1 on the payload model itself rejects an outright empty
    # string before the manager (and its strip-then-validate ValueError)
    # ever runs -- this is FastAPI's own request validation, hence 422 not
    # 400.
    res = client.post("/api/class/plans", json={"name": "", "plan": _SEGMENTS_A})
    assert res.status_code == 422


def test_post_class_plans_name_over_60_chars_is_422():
    res = client.post("/api/class/plans", json={"name": "x" * 61, "plan": _SEGMENTS_A})
    assert res.status_code == 422


def test_delete_class_plans_removes_and_returns_the_list():
    client.post("/api/class/plans", json={"name": "Spin 45", "plan": _SEGMENTS_A})
    res = client.delete("/api/class/plans/Spin 45")
    assert res.status_code == 200
    assert res.json() == {"plans": []}
    assert app_module.race_manager.list_class_plans() == {}


def test_delete_class_plans_missing_name_is_404():
    res = client.delete("/api/class/plans/Nonexistent")
    assert res.status_code == 404


def test_class_plans_endpoints_require_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")

    assert client.get("/api/class/plans").status_code == 401
    assert (
        client.post(
            "/api/class/plans", json={"name": "Spin 45", "plan": _SEGMENTS_A}
        ).status_code
        == 401
    )

    ok_headers = {"X-FitRace-Admin-Token": "admin-secret"}
    res = client.post(
        "/api/class/plans",
        json={"name": "Spin 45", "plan": _SEGMENTS_A},
        headers=ok_headers,
    )
    assert res.status_code == 200

    assert (
        client.delete("/api/class/plans/Spin 45", headers=ok_headers).status_code == 200
    )
    assert client.get("/api/class/plans", headers=ok_headers).status_code == 200


def test_class_plans_endpoint_does_not_appear_in_race_state_snapshot():
    # /api/race/state is broadcast on every tick -- the library must not
    # ride along on it.
    client.post("/api/class/plans", json={"name": "Spin 45", "plan": _SEGMENTS_A})
    state = client.get("/api/race/state").json()
    assert "class_plans" not in state


def _expected_plan(segments_payload: dict) -> dict:
    return ClassPlan.model_validate(segments_payload).model_dump()
