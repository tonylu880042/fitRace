import time

from fastapi.testclient import TestClient
from hub_server.infrastructure.fastapi.app import app

client = TestClient(app)


def test_health_check_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.1"}


def test_locales_endpoint_defaults_to_english_and_lists_supported_locales():
    response = client.get("/api/locales")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_locale"] == "en-US"
    assert [locale["code"] for locale in payload["locales"]] == [
        "en-US",
        "zh-TW",
        "it",
        "fr",
        "de-CH",
        "sv",
    ]


def test_locale_endpoint_returns_messages():
    response = client.get("/api/locales/zh-TW")

    assert response.status_code == 200
    payload = response.json()
    assert payload["locale"] == "zh-TW"
    assert payload["messages"]["settings.language"] == "系統語系"


def test_signup_page_has_language_switcher_defaulting_to_english():
    response = client.get("/static/signup.html")

    assert response.status_code == 200
    assert '<html lang="en-US">' in response.text
    assert 'id="language-select"' in response.text
    assert 'Athlete Self-Registration' in response.text
    assert 'Deutsch (Schweiz)' in response.text
    assert 'Svenska' in response.text


def test_dashboard_includes_system_power_controls():
    response = client.get("/static/index.html")

    assert response.status_code == 200
    assert "System Power" in response.text
    assert "Restart Hub Service" in response.text
    assert "Shutdown Hub" in response.text


def test_nodes_endpoint_returns_registered_edge_nodes():
    from hub_server.infrastructure.fastapi.app import node_registry

    node_registry.clear()
    node_registry.update_status(
        {
            "edge_node_id": "fitrace-edge-01",
            "hostname": "fitrace-edge-01",
            "ip": "192.168.26.101",
            "status": "online",
            "last_seen_epoch_ms": 1_000_000,
            "equipment_streams": [
                {
                    "node_id": "fitrace-edge-01-bike-01",
                    "equipment_id": "BIKE_01",
                    "equipment_type": "fan_bike",
                    "status": "configured",
                }
            ],
        }
    )

    response = client.get("/api/nodes")
    assert response.status_code == 200
    assert response.json()["nodes"][0]["edge_node_id"] == "fitrace-edge-01"


def test_power_status_endpoint_defaults_to_dry_run():
    client.post("/api/race/reset")

    response = client.get("/api/system/power/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["power_actions_allowed"] is True
    assert payload["service_name"] == "fitracestudio-hub.service"


def test_update_status_endpoint_reports_last_check(monkeypatch):
    from hub_server.infrastructure.fastapi.app import update_checker

    monkeypatch.setattr(
        update_checker,
        "status",
        lambda: {"state": "never_checked", "update_available": False},
    )

    response = client.get("/api/updates/status")

    assert response.status_code == 200
    assert response.json() == {"state": "never_checked", "update_available": False}


def test_update_check_endpoint_runs_manual_check(monkeypatch):
    from hub_server.infrastructure.fastapi.app import update_checker

    monkeypatch.setattr(
        update_checker,
        "check",
        lambda: {
            "state": "available",
            "update_available": True,
            "latest_hub_version": "0.1.1",
        },
    )

    response = client.post("/api/updates/check")

    assert response.status_code == 200
    assert response.json()["state"] == "available"
    assert response.json()["latest_hub_version"] == "0.1.1"


def test_update_download_endpoint_downloads_artifacts(monkeypatch):
    from hub_server.infrastructure.fastapi.app import update_checker

    monkeypatch.setattr(
        update_checker,
        "download",
        lambda: {
            "state": "downloaded",
            "artifacts": {
                "hub": {"sha256_verified": True},
                "edge": {"sha256_verified": True},
            },
        },
    )

    response = client.post("/api/updates/download")

    assert response.status_code == 200
    assert response.json()["state"] == "downloaded"
    assert response.json()["artifacts"]["hub"]["sha256_verified"] is True


def test_update_install_hub_endpoint_installs_when_idle(monkeypatch):
    from hub_server.infrastructure.fastapi.app import update_checker

    client.post("/api/race/reset")
    monkeypatch.setattr(
        update_checker,
        "install_hub",
        lambda: {
            "state": "hub_installed",
            "hub_install": {"version": "0.1.1", "service_restart": "not_run"},
        },
    )

    response = client.post("/api/updates/install/hub")

    assert response.status_code == 200
    assert response.json()["state"] == "hub_installed"
    assert response.json()["hub_install"]["service_restart"] == "not_run"


def test_update_install_hub_endpoint_blocks_while_race_running(monkeypatch):
    client.post("/api/race/reset")
    client.post(
        "/api/race/configure",
        json={"race_type": "time", "target_value": 0, "duration_sec": 60},
    )
    client.post("/api/race/start")

    response = client.post("/api/updates/install/hub")

    assert response.status_code == 409
    assert "IDLE" in response.json()["detail"]
    client.post("/api/race/reset")


def test_update_apply_hub_endpoint_starts_updater_service_when_idle(monkeypatch):
    from hub_server.infrastructure.fastapi import app as hub_app

    client.post("/api/race/reset")
    calls = []
    monkeypatch.setattr(hub_app, "run_systemctl", lambda command: calls.append(command))

    response = client.post("/api/updates/apply/hub")

    assert response.status_code == 200
    assert response.json()["state"] == "updater_started"
    assert calls == [["systemctl", "start", "fitracestudio-hub-updater.service"]]


def test_hub_checks_updates_once_on_startup(monkeypatch):
    from hub_server.infrastructure.fastapi.app import update_checker

    calls = []
    monkeypatch.setenv("FITRACE_UPDATE_AUTO_CHECK", "1")
    monkeypatch.setattr(update_checker, "check", lambda: calls.append("checked") or {})

    with TestClient(app) as startup_client:
        startup_client.get("/health")
        for _ in range(20):
            if calls:
                break
            time.sleep(0.01)

    assert calls == ["checked"]


def test_power_shutdown_requires_confirmation_and_does_not_execute_in_dry_run():
    client.post("/api/race/reset")

    missing_confirmation = client.post("/api/system/power/shutdown", json={})
    assert missing_confirmation.status_code == 409

    response = client.post(
        "/api/system/power/shutdown",
        json={"confirmation": "SHUTDOWN"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["executed"] is False
    assert payload["command"] == ["systemctl", "poweroff"]


def test_power_actions_are_blocked_while_race_is_running():
    client.post("/api/race/reset")
    client.post(
        "/api/race/configure",
        json={"race_type": "time", "target_value": 0, "duration_sec": 60},
    )
    client.post("/api/race/start")

    response = client.post("/api/system/power/reboot", json={"confirmation": "REBOOT"})

    assert response.status_code == 409
    assert "IDLE" in response.json()["detail"]
    client.post("/api/race/reset")


def test_race_workflow_via_api():
    # Initial state should be IDLE
    res = client.get("/api/race/state")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"

    # Configure race
    config_payload = {"race_type": "time", "target_value": 0, "duration_sec": 120}
    res = client.post("/api/race/configure", json=config_payload)
    assert res.status_code == 200
    assert res.json()["state"] == "READY"
    assert res.json()["config"]["duration_sec"] == 120

    # Start race
    res = client.post("/api/race/start")
    assert res.status_code == 200
    assert res.json()["state"] == "RUNNING"

    # Stop race
    res = client.post("/api/race/stop")
    assert res.status_code == 200
    assert res.json()["state"] == "STOPPED"

    # Reset race
    res = client.post("/api/race/reset")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"


def test_race_close_endpoint_via_api():
    client.post("/api/race/reset")

    config_payload = {"race_type": "distance", "target_value": 100, "duration_sec": 0}
    res = client.post("/api/race/configure", json=config_payload)
    assert res.status_code == 200

    res = client.post("/api/race/start")
    assert res.status_code == 200
    assert res.json()["state"] == "RUNNING"

    telemetry_payload = {
        "node_id": "bike-01",
        "equipment_type": "bike",
        "distance_m": 40,
        "elapsed_time_ms": 12000,
    }
    res = client.post("/api/test/telemetry", json=telemetry_payload)
    assert res.status_code == 200

    res = client.post("/api/race/close")
    assert res.status_code == 200
    data = res.json()
    assert data["state"] == "STOPPED"
    assert data["end_time_epoch_ms"] is not None
    assert data["leaderboard"]["bike-01"]["distance_m"] == 40
    assert data["leaderboard"]["bike-01"]["finished_time_ms"] is None


def test_websocket_dashboard_broadcast():
    config_payload = {"race_type": "distance", "target_value": 1000, "duration_sec": 0}
    # Configure and start first
    client.post("/api/race/configure", json=config_payload)
    client.post("/api/race/start")

    # Connect via WebSocket
    with client.websocket_connect("/ws/dashboard") as websocket:
        # Simulate pushing MQTT telemetry from background.
        # We can trigger it by sending a POST request to update telemetry (for API design simplicity).
        # We'll expose a POST /api/test/telemetry endpoint in development mode for easy API test triggering.
        telemetry_payload = {
            "node_id": "rower-01",
            "equipment_id": "ROW_01",
            "equipment_type": "rowing_machine",
            "instantaneous_speed_kph": 10.0,
            "cadence_rpm": 30,
            "power_watts": 180,
            "heart_rate_bpm": 135,
            "distance_m": 50.0,
            "elapsed_time_ms": 10000,
            "timestamp_epoch_ms": 1600000000000,
        }
        res = client.post("/api/test/telemetry", json=telemetry_payload)
        assert res.status_code == 200

        # Receive payload via websocket
        data = websocket.receive_json()
        assert data["rower-01"]["distance_m"] == 50.0
        assert data["rower-01"]["progress_percent"] == 5.0  # 50 / 1000 * 100
