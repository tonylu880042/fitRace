"""Game Admin toggle "show live data while idle" -- persisted the same way
signup_qr_visible/admin_qr_visible are (see test_dashboard_qr_visibility.py
for the RaceManager-level contract). This file covers the HTTP surface:
/api/dashboard/idle-telemetry-visibility.
"""

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app

client = TestClient(app)


def test_idle_telemetry_visibility_defaults_true_and_is_present_in_race_state():
    client.post("/api/race/reset")
    state = client.get("/api/race/state")
    assert state.status_code == 200
    assert state.json()["idle_live_telemetry_visible"] is True


def test_idle_telemetry_visibility_endpoint_updates_the_flag():
    client.post("/api/race/reset")
    try:
        off = client.post(
            "/api/dashboard/idle-telemetry-visibility", json={"visible": False}
        )
        assert off.status_code == 200
        assert off.json()["idle_live_telemetry_visible"] is False

        state = client.get("/api/race/state")
        assert state.json()["idle_live_telemetry_visible"] is False

        on = client.post(
            "/api/dashboard/idle-telemetry-visibility", json={"visible": True}
        )
        assert on.status_code == 200
        assert on.json()["idle_live_telemetry_visible"] is True
    finally:
        # Durable venue configuration -- restore the suite-wide default so
        # later tests asserting the default aren't affected by this one.
        client.post("/api/dashboard/idle-telemetry-visibility", json={"visible": True})


def test_idle_telemetry_visibility_rejects_non_bool_with_422():
    client.post("/api/race/reset")
    res = client.post(
        "/api/dashboard/idle-telemetry-visibility", json={"visible": "not-a-bool"}
    )
    assert res.status_code == 422


def test_idle_telemetry_visibility_requires_admin_token(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
    res = client.post(
        "/api/dashboard/idle-telemetry-visibility", json={"visible": False}
    )
    assert res.status_code == 401
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
