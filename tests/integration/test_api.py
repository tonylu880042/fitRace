import time

from fastapi.testclient import TestClient
from hub_server.infrastructure.fastapi.app import app

client = TestClient(app)


def set_online_station(station_number: int, node_id: str):
    from hub_server.infrastructure.fastapi.app import node_registry

    node_registry.update_status(
        {
            "edge_node_id": f"edge-{station_number:02d}",
            "status": "online",
            "last_seen_epoch_ms": int(time.time() * 1000),
            "equipment_streams": [
                {
                    "node_id": node_id,
                    "equipment_id": f"BIKE_{station_number:02d}",
                    "equipment_type": "fan_bike",
                    "status": "configured",
                    "last_telemetry_epoch_ms": int(time.time() * 1000),
                }
            ],
        }
    )
    client.post(
        "/api/stations/assign",
        json={"station_number": station_number, "node_id": node_id},
    )


def prepare_individual_ready_race():
    client.post("/api/race/reset")
    set_online_station(1, "node-01")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )
    return client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 100, "duration_sec": 0},
    )


def test_hub_management_endpoints_require_admin_token(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")

    response = client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 100, "duration_sec": 0},
    )
    assert response.status_code == 401

    response = client.post(
        "/api/race/configure",
        headers={"X-FitRace-Admin-Token": "admin-secret"},
        json={"race_type": "distance", "target_value": 100, "duration_sec": 0},
    )
    assert response.status_code == 200

    blocked_routes = [
        ("/api/race/start", {}),
        ("/api/race/countdown-start", {}),
        ("/api/race/stop", {}),
        ("/api/race/close", {}),
        ("/api/race/reset", {}),
        ("/api/stations/assign", {"station_number": 1, "node_id": "node-01"}),
        ("/api/leaderboard/display", {"mode": "classic"}),
        ("/api/race/start-sound", {"enabled": True}),
    ]
    for route, payload in blocked_routes:
        response = client.post(route, json=payload)
        assert response.status_code == 401


def test_start_race_blocks_station_without_fresh_telemetry_timestamp():
    from hub_server.infrastructure.fastapi.app import node_registry

    client.post("/api/race/reset")
    node_registry.clear()
    node_registry.update_status(
        {
            "edge_node_id": "edge-01",
            "status": "online",
            "last_seen_epoch_ms": int(time.time() * 1000),
            "equipment_streams": [
                {
                    "node_id": "node-01",
                    "equipment_id": "BIKE_01",
                    "equipment_type": "fan_bike",
                    "status": "configured",
                }
            ],
        }
    )
    client.post(
        "/api/stations/assign",
        json={"station_number": 1, "node_id": "node-01"},
    )
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )
    client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 100, "duration_sec": 0},
    )

    readiness = client.get("/api/race/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    assert readiness.json()["station_health"][0]["health"] == "missing"

    blocked = client.post("/api/race/start")
    assert blocked.status_code == 409


def test_station_assignment_is_blocked_while_race_is_running():
    client.post("/api/race/reset")
    set_online_station(1, "node-01")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )
    client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 100, "duration_sec": 0},
    )
    started = client.post("/api/race/start")
    assert started.status_code == 200

    response = client.post(
        "/api/stations/assign",
        json={"station_number": 1, "node_id": None},
    )

    assert response.status_code == 409


def test_health_check_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.2"}


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
    assert "function convertAvatarFileToWebp" in response.text
    assert "function convertAvatarSourceToWebp" in response.text
    assert "AVATAR_MAX_SOURCE_BYTES = 8 * 1024 * 1024" in response.text
    assert "AVATAR_TARGET_SIZE = 96" in response.text
    assert "AVATAR_MAX_OUTPUT_BYTES = 32 * 1024" in response.text
    assert "estimateDataUrlBytes" in response.text
    assert "avatarProcessing" in response.text
    assert "canvas.toDataURL('image/webp', quality)" in response.text


def test_index_has_finish_celebration_and_record_wall_markers():
    response_index = client.get("/static/index.html")
    assert response_index.status_code == 200
    # A2: finish-line flash banner queued per athlete finish.
    assert "finish-banner" in response_index.text
    assert "queueFinishBanner" in response_index.text
    assert "detectAthleteFinishes" in response_index.text
    # A2: full-screen podium reveal on a live RUNNING -> STOPPED transition.
    assert "podium-overlay" in response_index.text
    assert "showPodiumOverlay" in response_index.text
    assert "hasReceivedFirstState" in response_index.text
    # B2: idle record wall carousel.
    assert 'id="record-wall"' in response_index.text
    assert "/api/results/records" in response_index.text
    assert "enterIdleRecordWall" in response_index.text
    assert "exitIdleRecordWall" in response_index.text
    # New UI strings are translated via the i18n helper, not hardcoded.
    assert 't("finish.exclaim")' in response_index.text
    assert 't("podium.final_results")' in response_index.text
    assert 't("record_wall.title")' in response_index.text
    # No forbidden strings from the removed management controls should sneak back in.
    assert "enableDashboardSound" not in response_index.text
    assert 'id="sound-control"' not in response_index.text
    assert "id=\"leaderboard-display-mode\"" not in response_index.text
    assert "System Power" not in response_index.text


def test_management_controls_are_split_by_admin_role():
    response_index = client.get("/static/index.html")
    assert response_index.status_code == 200
    assert "renderTeamLeaderboard" in response_index.text
    assert "renderRaceTrackLeaderboard" in response_index.text
    assert "renderTeamBattleLeaderboard" in response_index.text
    assert "renderSprintBoardLeaderboard" in response_index.text
    assert "competition_mode === \"team\"" in response_index.text
    assert "team-leaderboard-item" in response_index.text
    assert "race-track-item" in response_index.text
    assert "team-battle-card" in response_index.text
    assert "race-stage-banner" in response_index.text
    assert "renderRaceStageBanner" in response_index.text
    assert "team-battle-status" in response_index.text
    assert "sprint-board-card" in response_index.text
    assert "/static/audio/countdown_start.wav" in response_index.text
    assert "playDashboardRaceCountdown" in response_index.text
    assert "race_countdown" in response_index.text
    assert "enableDashboardSound" not in response_index.text
    assert 'id="sound-control"' not in response_index.text
    assert "id=\"leaderboard-display-mode\"" not in response_index.text
    assert "System Power" not in response_index.text

    response_signup = client.get("/static/signup.html")
    assert response_signup.status_code == 200
    assert "Race Control" not in response_signup.text
    assert "System Power" not in response_signup.text
    assert "Restart Hub Service" not in response_signup.text
    assert "Shutdown Hub" not in response_signup.text

    response_admin = client.get("/static/admin.html")
    assert response_admin.status_code == 200
    assert "Game Admin" in response_admin.text
    assert "System Admin" in response_admin.text
    assert "Race Control" not in response_admin.text
    assert "System Power" not in response_admin.text

    response_game_admin = client.get("/static/gameAdmin.html")
    assert response_game_admin.status_code == 200
    assert "Race Control" in response_game_admin.text
    assert "Station Status" in response_game_admin.text
    assert 'id="competition-mode"' in response_game_admin.text
    assert 'id="team-scoring-policy"' in response_game_admin.text
    assert 'id="team-completion-policy"' in response_game_admin.text
    assert 'id="leaderboard-display-mode"' in response_game_admin.text
    assert "setLeaderboardDisplayMode" in response_game_admin.text
    assert "/api/leaderboard/display" in response_game_admin.text
    assert "/api/race/countdown-start" in response_game_admin.text
    assert 'id="start-sound-enabled"' in response_game_admin.text
    assert "/api/race/start-sound" in response_game_admin.text
    assert "setStartCountdownSound" in response_game_admin.text
    assert "Team Rule" in response_game_admin.text
    assert "Leaderboard Preview" in response_game_admin.text
    assert "Start Flow" in response_game_admin.text
    assert "Race Readiness" in response_game_admin.text
    assert "/api/race/readiness" in response_game_admin.text
    assert "renderReadinessPanel" in response_game_admin.text
    assert "Station Health" in response_game_admin.text
    assert "Team Average" in response_game_admin.text
    assert "Team Target" in response_game_admin.text
    assert "Everyone Finishes" in response_game_admin.text
    assert "Countdown Active" in response_game_admin.text
    assert "Everyone Finishes means every teammate must complete" in response_game_admin.text
    assert "Operator Unlock" in response_game_admin.text
    assert "Access Code" in response_game_admin.text
    assert "Admin Token" not in response_game_admin.text
    assert "/static/audio/countdown_start.wav" not in response_game_admin.text
    assert "playRaceStartCountdown" not in response_game_admin.text
    assert "competition_mode: competitionMode" in response_game_admin.text
    assert "team_scoring_policy: teamScoringPolicy" in response_game_admin.text
    assert "team_completion_policy: teamCompletionPolicy" in response_game_admin.text
    assert "renderTeamReadiness" not in response_game_admin.text
    assert '<option value="0">Manual</option>' not in response_game_admin.text
    assert "Station Assignment" not in response_game_admin.text
    assert "Assign Stream" not in response_game_admin.text
    assert "Unassign Station" not in response_game_admin.text
    assert "System Power" not in response_game_admin.text
    assert "Restart Hub Service" not in response_game_admin.text

    response_system_admin = client.get("/static/systemAdmin.html")
    assert response_system_admin.status_code == 200
    assert "Edge Nodes" in response_system_admin.text
    assert "Station Assignment" in response_system_admin.text
    assert ">Assign</button>" in response_system_admin.text
    assert "Unassign station" in response_system_admin.text
    assert "Software" in response_system_admin.text
    assert "Power Controls" in response_system_admin.text
    assert "Maintenance Unlock" in response_system_admin.text
    assert "Access Code" in response_system_admin.text
    assert "Admin Token" not in response_system_admin.text
    assert "Race Control" not in response_system_admin.text

    assert client.get("/gameAdmin", follow_redirects=False).headers["location"] == "/static/gameAdmin.html"
    assert client.get("/systemAdmin", follow_redirects=False).headers["location"] == "/static/systemAdmin.html"


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
    assert calls == [["sudo", "systemctl", "start", "fitracestudio-hub-updater.service"]]


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


def test_power_shutdown_requires_confirmation_and_does_not_execute_in_dry_run(monkeypatch):
    client.post("/api/race/reset")

    missing_confirmation = client.post("/api/system/power/shutdown", json={})
    assert missing_confirmation.status_code == 409

    class MockMqttClient:
        def __init__(self):
            self.published = []

        async def publish(self, topic, payload):
            self.published.append((topic, payload))

    mock_mqtt = MockMqttClient()
    from hub_server.infrastructure.fastapi.app import app
    app.state.mqtt_client = mock_mqtt

    response = client.post(
        "/api/system/power/shutdown",
        json={"confirmation": "SHUTDOWN"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["executed"] is False
    assert payload["command"] == ["sudo", "systemctl", "poweroff"]

    assert len(mock_mqtt.published) == 1
    topic, msg = mock_mqtt.published[0]
    assert topic == "fitrace/nodes/command"
    assert "shutdown" in msg


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
    from hub_server.infrastructure.fastapi.app import node_registry

    client.post("/api/race/reset")
    node_registry.clear()
    res = client.get("/api/race/state")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"


def test_stopped_race_result_is_persisted_and_listed(monkeypatch, tmp_path):
    import hub_server.infrastructure.fastapi.app as hub_app
    from hub_server.usecases.race_result_store import RaceResultStore

    store = RaceResultStore(tmp_path / "race_results.jsonl")
    monkeypatch.setattr(hub_app, "race_result_store", store)

    client.post("/api/race/reset")
    set_online_station(1, "node-01")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )
    client.post(
        "/api/race/configure",
        json={"race_type": "time", "target_value": 0, "duration_sec": 120},
    )
    client.post("/api/race/start")

    stop_response = client.post("/api/race/stop")
    results_response = client.get("/api/race/results")

    assert stop_response.status_code == 200
    assert results_response.status_code == 200
    results = results_response.json()["results"]
    assert len(results) == 1
    assert results[0]["snapshot"]["state"] == "STOPPED"
    assert results[0]["snapshot"]["config"]["race_type"] == "time"
    assert results[0]["snapshot"]["leaderboard"]["node-01"]["athlete_name"] == "Runner A"
    client.post("/api/race/reset")

    set_online_station(1, "node-01")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )

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


def test_race_readiness_reports_blocking_issues_before_start():
    from hub_server.infrastructure.fastapi.app import node_registry

    client.post("/api/race/reset")
    node_registry.clear()
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "target_value": 100,
            "duration_sec": 0,
            "competition_mode": "team",
            "team_scoring_policy": "average",
            "team_completion_policy": "aggregate",
        },
    )

    readiness = client.get("/api/race/readiness")

    assert readiness.status_code == 200
    payload = readiness.json()
    assert payload["ready"] is False
    assert "Register at least one athlete before starting." in payload["blocking_issues"]
    assert "Team race needs at least two teams." in payload["blocking_issues"]
    assert payload["checks"]["sound"]["status"] == "ok"

    blocked = client.post("/api/race/start")
    assert blocked.status_code == 409
    assert "Register at least one athlete" in blocked.json()["detail"]


def test_race_readiness_passes_for_online_registered_team_race():
    from hub_server.infrastructure.fastapi.app import node_registry

    client.post("/api/race/reset")
    node_registry.clear()
    set_online_station(1, "node-01")
    set_online_station(2, "node-02")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A", "team_name": "Volt"},
    )
    client.post(
        "/api/race/register",
        json={"station_number": 2, "athlete_name": "Runner B", "team_name": "Apex"},
    )
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "target_value": 100,
            "duration_sec": 0,
            "competition_mode": "team",
            "team_scoring_policy": "total",
            "team_completion_policy": "all_members",
        },
    )

    readiness = client.get("/api/race/readiness")

    assert readiness.status_code == 200
    payload = readiness.json()
    assert payload["ready"] is True
    assert payload["blocking_issues"] == []
    assert payload["checks"]["teams"]["status"] == "ok"
    assert payload["checks"]["stations"]["status"] == "ok"
    assert [station["health"] for station in payload["station_health"]] == ["online", "online"]


def test_start_race_blocks_registered_station_without_online_device():
    from hub_server.infrastructure.fastapi.app import node_registry

    client.post("/api/race/reset")
    node_registry.clear()
    client.post(
        "/api/stations/assign",
        json={"station_number": 1, "node_id": "missing-node"},
    )
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )
    client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 100, "duration_sec": 0},
    )

    blocked = client.post("/api/race/countdown-start")

    assert blocked.status_code == 409
    assert "Station 1 device is missing or offline." in blocked.json()["detail"]
    client.post("/api/race/reset")


def test_leaderboard_display_mode_can_be_controlled_from_game_admin():
    client.post("/api/race/reset")

    default_state = client.get("/api/race/state")
    assert default_state.status_code == 200
    assert default_state.json()["leaderboard_display_mode"] == "classic"

    res = client.post("/api/leaderboard/display", json={"mode": "team_battle"})
    assert res.status_code == 200
    assert res.json()["leaderboard_display_mode"] == "team_battle"

    state = client.get("/api/race/state")
    assert state.json()["leaderboard_display_mode"] == "team_battle"

    invalid = client.post("/api/leaderboard/display", json={"mode": "manual"})
    assert invalid.status_code == 400
    client.post("/api/leaderboard/display", json={"mode": "classic"})


def test_countdown_start_delays_race_start_for_dashboard_audio(monkeypatch):
    from hub_server.infrastructure.fastapi import app as hub_app
    from hub_server.infrastructure.fastapi.app import node_registry

    client.post("/api/race/reset")
    node_registry.clear()
    monkeypatch.setattr(hub_app, "RACE_START_COUNTDOWN_DURATION_MS", 10)
    broadcasts = []

    async def capture_broadcast(payload):
      broadcasts.append(payload)

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", capture_broadcast)
    set_online_station(1, "node-01")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )
    broadcasts.clear()
    client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 100, "duration_sec": 0},
    )

    res = client.post("/api/race/countdown-start")

    assert res.status_code == 200
    assert res.json()["state"] == "RUNNING"
    assert any(
        payload.get("type") == "race_countdown" and payload.get("play_sound") is True
        for payload in broadcasts
    )
    client.post("/api/race/reset")


def test_start_countdown_sound_defaults_on_and_can_be_controlled_from_game_admin():
    client.post("/api/race/reset")

    state = client.get("/api/race/state")
    assert state.status_code == 200
    assert state.json()["start_countdown_sound_enabled"] is True

    disabled = client.post("/api/race/start-sound", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["start_countdown_sound_enabled"] is False

    enabled = client.post("/api/race/start-sound", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["start_countdown_sound_enabled"] is True


def test_team_race_state_exposes_team_leaderboard(monkeypatch):
    from hub_server.infrastructure.fastapi.app import node_registry

    monkeypatch.setenv("TESTING", "1")
    client.post("/api/race/reset")
    node_registry.clear()
    for station_number in (1, 2, 3):
        client.post(
            "/api/stations/assign",
            json={"station_number": station_number, "node_id": None},
        )
    set_online_station(1, "node-01")
    set_online_station(2, "node-02")
    set_online_station(3, "node-03")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A", "team_name": "Volt"},
    )
    client.post(
        "/api/race/register",
        json={"station_number": 2, "athlete_name": "Runner B", "team_name": "Volt"},
    )
    client.post(
        "/api/race/register",
        json={"station_number": 3, "athlete_name": "Runner C", "team_name": "Apex"},
    )

    res = client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "target_value": 100,
            "duration_sec": 0,
            "competition_mode": "team",
            "team_scoring_policy": "total",
            "team_completion_policy": "all_members",
        },
    )
    assert res.status_code == 200
    assert res.json()["config"]["competition_mode"] == "team"

    client.post("/api/race/start")
    client.post(
        "/api/test/telemetry",
        json={"node_id": "node-01", "distance_m": 80, "elapsed_time_ms": 10000},
    )
    client.post(
        "/api/test/telemetry",
        json={"node_id": "node-02", "distance_m": 40, "elapsed_time_ms": 10000},
    )
    client.post(
        "/api/test/telemetry",
        json={"node_id": "node-03", "distance_m": 50, "elapsed_time_ms": 10000},
    )

    state = client.get("/api/race/state").json()

    assert state["config"]["team_scoring_policy"] == "total"
    assert state["config"]["team_completion_policy"] == "all_members"
    assert [team["team_name"] for team in state["team_leaderboard"]] == ["Volt", "Apex"]
    assert state["team_leaderboard"][0]["team_finished"] is False
    assert state["team_leaderboard"][0]["score_value"] == 60.0
    assert state["team_leaderboard"][0]["member_count"] == 2
    assert [member["station_number"] for member in state["team_leaderboard"][0]["members"]] == [1, 2]
    client.post("/api/race/reset")
    for station_number in (1, 2, 3):
        client.post(
            "/api/stations/assign",
            json={"station_number": station_number, "node_id": None},
        )


def test_race_configure_rejects_invalid_config_and_keeps_idle_state():
    client.post("/api/race/reset")

    res = client.post(
        "/api/race/configure",
        json={"race_type": "mystery", "target_value": 100, "duration_sec": 0},
    )
    assert res.status_code == 400
    assert client.get("/api/race/state").json()["state"] == "IDLE"

    res = client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 0, "duration_sec": 0},
    )
    assert res.status_code == 400
    assert client.get("/api/race/state").json()["state"] == "IDLE"

    res = client.post(
        "/api/race/configure",
        json={"race_type": "time", "target_value": 0, "duration_sec": 0},
    )
    assert res.status_code == 400
    assert client.get("/api/race/state").json()["state"] == "IDLE"


def test_test_telemetry_endpoint_is_disabled_without_explicit_test_mode(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("FITRACE_ENABLE_TEST_TELEMETRY", raising=False)

    res = client.post(
        "/api/test/telemetry",
        json={"node_id": "bike-01", "equipment_type": "bike"},
    )

    assert res.status_code == 404


def test_diagnostic_telemetry_endpoint_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FITRACE_ENABLE_DIAGNOSTICS", raising=False)

    res = client.post(
        "/api/diagnostics/telemetry",
        json={"node_id": "diagnostic-bike-01"},
    )

    assert res.status_code == 404


def test_diagnostic_telemetry_requires_admin_token(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_DIAGNOSTICS", "1")
    monkeypatch.setenv("FITRACE_DIAGNOSTICS_TOKEN", "secret")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)

    res = client.post(
        "/api/diagnostics/telemetry",
        json={"node_id": "diagnostic-bike-01"},
    )

    assert res.status_code == 401


def test_diagnostic_telemetry_blocks_while_race_is_running(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_DIAGNOSTICS", "1")
    monkeypatch.setenv("FITRACE_DIAGNOSTICS_TOKEN", "secret")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    prepare_individual_ready_race()
    client.post(
        "/api/race/configure",
        json={"race_type": "time", "target_value": 0, "duration_sec": 60},
    )
    client.post("/api/race/start")

    res = client.post(
        "/api/diagnostics/telemetry",
        json={"node_id": "diagnostic-bike-01"},
        headers={"X-FitRace-Diagnostics-Token": "secret"},
    )

    assert res.status_code == 409
    assert "running" in res.json()["detail"].lower()
    client.post("/api/race/reset")


def test_diagnostic_telemetry_broadcasts_synthetic_progress_without_mutating_race_state(
    monkeypatch,
):
    from hub_server.infrastructure.fastapi import app as hub_app

    monkeypatch.setenv("FITRACE_ENABLE_DIAGNOSTICS", "1")
    monkeypatch.setenv("FITRACE_DIAGNOSTICS_TOKEN", "secret")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client.post("/api/race/reset")
    broadcasts = []

    async def capture_broadcast(message):
        broadcasts.append(message)

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", capture_broadcast)

    res = client.post(
        "/api/diagnostics/telemetry",
        json={
            "node_id": "diagnostic-bike-01",
            "equipment_type": "fan_bike",
            "distance_m": 25,
            "elapsed_time_ms": 5000,
            "power_watts": 180,
        },
        headers={"X-FitRace-Diagnostics-Token": "secret"},
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "passed"
    assert payload["diagnostic"] is True
    assert payload["checks"]["race_manager"] == "ok"
    assert payload["checks"]["websocket_broadcast"] == "sent"
    assert payload["progress"]["diagnostic-bike-01"]["progress_percent"] == 25.0
    assert broadcasts[0]["diagnostic-bike-01"]["distance_m"] == 25
    assert broadcasts[1]["type"] == "diagnostic_telemetry"

    state = client.get("/api/race/state").json()
    assert state["state"] == "IDLE"
    assert state["leaderboard"] == {}


def test_race_close_endpoint_via_api(monkeypatch):
    from hub_server.infrastructure.fastapi.app import node_registry

    monkeypatch.setenv("TESTING", "1")
    client.post("/api/race/reset")
    node_registry.clear()
    set_online_station(1, "bike-01")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )

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


def test_websocket_dashboard_broadcast(monkeypatch):
    from hub_server.infrastructure.fastapi.app import node_registry

    monkeypatch.setenv("TESTING", "1")
    client.post("/api/race/reset")
    node_registry.clear()
    set_online_station(1, "rower-01")
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Runner A"},
    )
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


def test_system_ip_endpoint_returns_ip():
    response = client.get("/api/system/ip")
    assert response.status_code == 200
    data = response.json()
    assert "ip" in data
    assert isinstance(data["ip"], str)


def test_power_shutdown_system_notifies_nodes_and_shuts_down(monkeypatch):
    client.post("/api/race/reset")

    class MockMqttClient:
        def __init__(self):
            self.published = []

        async def publish(self, topic, payload):
            self.published.append((topic, payload))

    mock_mqtt = MockMqttClient()
    from hub_server.infrastructure.fastapi.app import app
    app.state.mqtt_client = mock_mqtt

    response = client.post("/api/system/power/shutdown-system")
    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["executed"] is False
    assert payload["command"] == ["sudo", "systemctl", "poweroff"]
    assert len(mock_mqtt.published) == 1
    topic, msg = mock_mqtt.published[0]
    assert topic == "fitrace/nodes/command"
    assert "shutdown" in msg


def _seed_results_store(monkeypatch, tmp_path):
    import hub_server.infrastructure.fastapi.app as hub_app
    from hub_server.usecases.race_result_store import RaceResultStore
    from hub_server.usecases.race_results_query import RaceResultsQuery

    store = RaceResultStore(tmp_path / "race_results.jsonl")
    monkeypatch.setattr(hub_app, "race_result_store", store)
    monkeypatch.setattr(hub_app, "race_results_query", RaceResultsQuery(store))

    store.save_finished_snapshot(
        {
            "state": "STOPPED",
            "config": {
                "race_type": "distance",
                "competition_mode": "individual",
                "team_scoring_policy": None,
                "target_value": 100,
                "duration_sec": 0,
            },
            "start_time_epoch_ms": 1000,
            "end_time_epoch_ms": 2000,
            "leaderboard": {
                "node-01": {
                    "node_id": "node-01",
                    "athlete_name": "Alice",
                    "station_number": 1,
                    "team_name": None,
                    "avatar_url": None,
                    "distance_m": 100,
                    "elapsed_time_ms": 60000,
                    "instantaneous_speed_kph": 0.0,
                    "progress_percent": 100.0,
                    "calories": 50.0,
                    "power_watts": 0,
                    "max_power_watts": 0,
                    "finished_time_ms": 5000,
                },
                "node-02": {
                    "node_id": "node-02",
                    "athlete_name": "Bob",
                    "station_number": 2,
                    "team_name": None,
                    "avatar_url": None,
                    "distance_m": 80,
                    "elapsed_time_ms": 60000,
                    "instantaneous_speed_kph": 0.0,
                    "progress_percent": 80.0,
                    "calories": 40.0,
                    "power_watts": 0,
                    "max_power_watts": 0,
                    "finished_time_ms": None,
                },
            },
            "team_leaderboard": None,
        }
    )
    return store


def test_results_races_endpoint_lists_newest_first(monkeypatch, tmp_path):
    _seed_results_store(monkeypatch, tmp_path)

    response = client.get("/api/results/races")

    assert response.status_code == 200
    races = response.json()["races"]
    assert len(races) == 1
    assert races[0]["result_id"] == "1000-2000-distance"
    assert races[0]["race_type"] == "distance"
    assert races[0]["athlete_count"] == 2


def test_results_race_detail_endpoint_returns_ranked_results(monkeypatch, tmp_path):
    _seed_results_store(monkeypatch, tmp_path)

    response = client.get("/api/results/races/1000-2000-distance")

    assert response.status_code == 200
    race = response.json()
    assert race["result_id"] == "1000-2000-distance"
    assert [r["athlete_name"] for r in race["results"]] == ["Alice", "Bob"]
    assert race["results"][0]["rank"] == 1
    assert "token" in race["results"][0]


def test_results_race_detail_endpoint_404_for_unknown_id(monkeypatch, tmp_path):
    _seed_results_store(monkeypatch, tmp_path)

    response = client.get("/api/results/races/does-not-exist")

    assert response.status_code == 404


def test_results_token_endpoint_returns_athlete_result(monkeypatch, tmp_path):
    _seed_results_store(monkeypatch, tmp_path)
    race = client.get("/api/results/races/1000-2000-distance").json()
    token = race["results"][0]["token"]

    response = client.get(f"/api/results/token/{token}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["athlete"]["athlete_name"] == "Alice"
    assert payload["race"]["result_id"] == "1000-2000-distance"
    assert payload["total_athletes"] == 2


def test_results_token_endpoint_404_for_unknown_token(monkeypatch, tmp_path):
    _seed_results_store(monkeypatch, tmp_path)

    response = client.get("/api/results/token/" + "0" * 12)

    assert response.status_code == 404


def test_results_records_endpoint_returns_top_three_for_distance_category(monkeypatch, tmp_path):
    _seed_results_store(monkeypatch, tmp_path)

    response = client.get("/api/results/records")

    assert response.status_code == 200
    payload = response.json()
    records = payload["records"]
    assert len(records) == 1
    assert records[0]["race_type"] == "distance"
    assert records[0]["label"] == "100 m"
    # Bob never finished (finished_time_ms is None), so only Alice qualifies.
    assert [e["athlete_name"] for e in records[0]["entries"]] == ["Alice"]
    assert records[0]["entries"][0]["value"] == 5000
    assert records[0]["entries"][0]["end_time_epoch_ms"] == 2000


def test_results_records_endpoint_returns_empty_records_with_no_stored_results(monkeypatch, tmp_path):
    import hub_server.infrastructure.fastapi.app as hub_app
    from hub_server.usecases.race_result_store import RaceResultStore
    from hub_server.usecases.race_results_query import RaceResultsQuery

    store = RaceResultStore(tmp_path / "empty_race_results.jsonl")
    monkeypatch.setattr(hub_app, "race_results_query", RaceResultsQuery(store))

    response = client.get("/api/results/records")

    assert response.status_code == 200
    assert response.json() == {"records": []}


def test_result_page_returns_200_with_result_card():
    response = client.get("/static/result.html")
    assert response.status_code == 200
    assert "id=\"result-card\"" in response.text
    assert 'data-lang="en"' in response.text
    assert "setLang" in response.text
    assert "/api/results/token/" in response.text
    assert "escapeHtml" in response.text


def test_results_wall_page_returns_200_with_qrcode_reference():
    response = client.get("/static/results.html")
    assert response.status_code == 200
    assert "/static/vendor/qrcode.min.js" in response.text
    assert "new QRCode" in response.text
    assert "/api/results/races" in response.text
    assert "id=\"results-container\"" in response.text
    assert 'data-lang="en"' in response.text
    assert "setLang" in response.text


def test_qrcode_library_returns_200():
    response = client.get("/static/vendor/qrcode.min.js")
    assert response.status_code == 200
    assert "QRCode" in response.text
    assert len(response.content) > 10000
