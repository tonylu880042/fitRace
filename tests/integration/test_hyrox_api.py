"""Integration tests for the resource-aware Hyrox HTTP API (Phase 6a)."""

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.infrastructure.fastapi.app import app, hyrox_service
from hub_server.usecases.hyrox_service import HyroxService


def _venue_body():
    return {
        "venue": {
            "venue_id": "hq",
            "course_profile_id": "hyrox_standard_2026",
            "resource_groups": [
                {
                    "group_id": "run_treadmills",
                    "resource_type": "ftms_machine_pool",
                    "stage_candidates": [],
                    "units": [{
                        "resource_id": "treadmill-01",
                        "display_name": "TM1",
                        "sensor_class": "ftms_machine",
                        "node_id": "edge-tm-01",
                        "entry_gate": {"node_id": "rfid-tm-01", "antenna_id": "T1_GATE"},
                    }],
                },
            ],
        },
        "mode": "training",
    }


def _assignment_venue_body():
    body = _venue_body()
    body["mode"] = "competition"
    body["venue"]["resource_groups"].append({
        "group_id": "shared_turf_lanes",
        "resource_type": "rfid_lane_pool",
        "stage_candidates": [],
        "units": [{
            "resource_id": "turf-lane-1",
            "display_name": "Lane 1",
            "sensor_class": "rfid_endpoint_pair",
            "start_endpoint": {"node_id": "rfid-01", "antenna_id": "L1_START"},
            "finish_endpoint": {"node_id": "rfid-01", "antenna_id": "L1_FINISH"},
        }],
    })
    return body


def test_hyrox_endpoints_404_when_disabled(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "0")
    client = TestClient(app)
    assert client.get("/api/hyrox/state").status_code == 404
    assert client.post("/api/hyrox/venue-config", json=_venue_body()).status_code == 404


def test_validate_venue_config_endpoint(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)

    resp = client.post("/api/hyrox/venue-config/validate", json=_venue_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["structural"] == []
    assert isinstance(body["readiness"], list)
    assert body["readiness"]  # single treadmill can't serve every stage


def test_get_venue_config_before_and_after_setup(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    # Isolated service instance: the module-level hyrox_service is a shared
    # singleton other tests configure, so a fresh one is required to observe
    # an honest "not yet configured" state regardless of test order.
    monkeypatch.setattr(hub_app, "hyrox_service", HyroxService())
    client = TestClient(app)

    before = client.get("/api/hyrox/venue-config")
    assert before.status_code == 200
    before_body = before.json()
    assert before_body["configured"] is False
    assert before_body["mode"] is None
    assert before_body["venue"] is None

    body = _venue_body()
    assert client.post("/api/hyrox/venue-config", json=body).status_code == 200

    after = client.get("/api/hyrox/venue-config")
    assert after.status_code == 200
    after_body = after.json()
    assert after_body["configured"] is True
    assert after_body["mode"] == "training"
    assert after_body["venue"]["venue_id"] == "hq"


def test_get_venue_config_requires_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(hub_app, "hyrox_service", HyroxService())
    client = TestClient(app)

    assert client.get("/api/hyrox/venue-config").status_code == 401
    headers = {"X-FitRace-Admin-Token": "secret"}
    assert client.get("/api/hyrox/venue-config", headers=headers).status_code == 200


def test_hyrox_venue_page_redirect(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)
    resp = client.get("/hyrox/venue", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/static/hyrox_venue_admin.html"


def test_venue_config_register_start_flow(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client = TestClient(app)

    # Registering before a venue is loaded is rejected.
    early = client.post("/api/hyrox/register", json={
        "athlete_name": "Alex", "rfid_tag_id": "TAG_ALEX"})
    assert early.status_code == 409

    # Load a venue config.
    resp = client.post("/api/hyrox/venue-config", json=_venue_body())
    assert resp.status_code == 200
    assert "readiness" in resp.json()  # incomplete venue -> readiness warnings

    # Register and start.
    assert client.post("/api/hyrox/register", json={
        "athlete_name": "Alex", "rfid_tag_id": "TAG_ALEX"}).status_code == 200
    assert client.post("/api/hyrox/start").status_code == 200

    state = client.get("/api/hyrox/state").json()
    assert state["is_active"] is True
    assert state["venue_configured"] is True
    assert state["subjects"][0]["subject_id"] == "TAG_ALEX"
    assert state["subjects"][0]["current_stage"] == "run_1"


def test_invalid_venue_config_is_rejected(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client = TestClient(app)
    body = _venue_body()
    # An rfid_endpoint_pair unit with no endpoints is structurally invalid.
    body["venue"]["resource_groups"].append({
        "group_id": "lanes", "resource_type": "rfid_lane_pool", "stage_candidates": [],
        "units": [{"resource_id": "lane-1", "display_name": "L1",
                   "sensor_class": "rfid_endpoint_pair"}],
    })
    assert client.post("/api/hyrox/venue-config", json=body).status_code == 400


def test_venue_config_validation_endpoint_returns_readiness(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)

    response = client.post("/api/hyrox/venue-config/validate", json=_venue_body())

    assert response.status_code == 200
    assert response.json()["structural"] == []
    assert "readiness" in response.json()


@pytest.mark.parametrize("division", ["solo", "open", "Individual"])
def test_registration_rejects_unknown_division(monkeypatch, division):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)

    response = client.post("/api/hyrox/register", json={
        "athlete_name": "Alex",
        "rfid_tag_id": "TAG_ALEX",
        "division": division,
    })

    assert response.status_code == 422


@pytest.mark.parametrize("mode", ["practice", "race", "Training"])
def test_venue_config_rejects_unknown_mode(monkeypatch, mode):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)
    body = _venue_body()
    body["mode"] = mode

    response = client.post("/api/hyrox/venue-config/validate", json=body)

    assert response.status_code == 422


@pytest.mark.parametrize("division", ["doubles", "relay"])
@pytest.mark.parametrize("team_name", [None, "", "   "])
def test_team_registration_requires_non_empty_team_name(
    monkeypatch, division, team_name,
):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)

    response = client.post("/api/hyrox/register", json={
        "athlete_name": "Alex",
        "rfid_tag_id": "TAG_ALEX",
        "division": division,
        "team_name": team_name,
    })

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("athlete_name", " "),
        ("athlete_name", "A" * 81),
        ("rfid_tag_id", " "),
        ("rfid_tag_id", "T" * 129),
        ("team_name", " "),
        ("team_name", "T" * 81),
    ],
)
def test_registration_rejects_invalid_text_fields(monkeypatch, field, value):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)
    body = {
        "athlete_name": "Alex",
        "rfid_tag_id": "TAG_ALEX",
        "division": "individual",
    }
    body[field] = value

    response = client.post("/api/hyrox/register", json=body)

    assert response.status_code == 422


def test_registration_trims_text_fields(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client = TestClient(app)
    assert client.post("/api/hyrox/venue-config", json=_venue_body()).status_code == 200

    response = client.post("/api/hyrox/register", json={
        "athlete_name": "  Alex  ",
        "rfid_tag_id": "  TAG_ALEX  ",
        "division": "doubles",
        "team_name": "  Team One  ",
    })
    individual_response = client.post("/api/hyrox/register", json={
        "athlete_name": "  Blair  ",
        "rfid_tag_id": "  TAG_BLAIR  ",
    })

    assert response.status_code == 200
    assert individual_response.status_code == 200
    state = client.get("/api/hyrox/state").json()
    assert len(state["subjects"]) == 2
    assert state["subjects"][0]["subject_id"] == "Team One"
    assert state["subjects"][0]["members"] == ["Alex"]
    assert state["subjects"][1]["subject_id"] == "TAG_BLAIR"
    assert state["subjects"][1]["members"] == ["Blair"]


def test_admin_endpoints_require_token_when_configured(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "secret")
    client = TestClient(app)

    assert client.post("/api/hyrox/venue-config", json=_venue_body()).status_code == 401
    assert client.post("/api/hyrox/start").status_code == 401
    assert client.post("/api/hyrox/complete-stage", json={"subject_id": "x"}).status_code == 401

    headers = {"X-FitRace-Admin-Token": "secret"}
    assert client.post("/api/hyrox/venue-config", json=_venue_body(),
                       headers=headers).status_code == 200
    # Registration stays open for self-service signup.
    assert client.post("/api/hyrox/register", json={
        "athlete_name": "Alex", "rfid_tag_id": "TAG_ALEX"}).status_code == 200


def test_god_view_endpoint(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client = TestClient(app)

    # Configure venue config and register athlete
    client.post("/api/hyrox/venue-config", json=_venue_body())
    client.post("/api/hyrox/register", json={
        "athlete_name": "Alex", "rfid_tag_id": "TAG_ALEX"})
    client.post("/api/hyrox/start")

    # Access god-view endpoint
    resp = client.get("/api/hyrox/god-view")
    assert resp.status_code == 200
    data = resp.json()
    assert data["venue_configured"] is True
    assert data["venue_id"] == "hq"
    assert len(data["resource_groups"]) == 1
    assert "treadmill-01" in data["resources"]
    assert data["resources"]["treadmill-01"]["status"] == "free"


def test_assignment_api_rejects_unknown_and_wrong_stage_resources(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client = TestClient(app)
    assert client.post(
        "/api/hyrox/venue-config", json=_assignment_venue_body()
    ).status_code == 200
    assert client.post("/api/hyrox/register", json={
        "athlete_name": "Alex",
        "rfid_tag_id": "TAG_ALEX",
    }).status_code == 200

    unknown_subject = client.post("/api/hyrox/assign", json={
        "subject_id": "missing",
        "resource_id": "treadmill-01",
    })
    unknown_resource = client.post("/api/hyrox/assign", json={
        "subject_id": "TAG_ALEX",
        "resource_id": "does-not-exist",
    })
    wrong_stage = client.post("/api/hyrox/assign", json={
        "subject_id": "TAG_ALEX",
        "resource_id": "turf-lane-1",
    })

    assert unknown_subject.status_code == 404
    assert unknown_subject.json()["detail"] == "Subject missing not found"
    assert unknown_resource.status_code == 404
    assert unknown_resource.json()["detail"] == "Resource does-not-exist not found"
    assert wrong_stage.status_code == 409
    assert "not allowed for stage run_1" in wrong_stage.json()["detail"]
    state = client.get("/api/hyrox/state").json()
    assert state["subjects"][0]["assigned_resource"] is None
    assert state["resources"]["turf-lane-1"] == "free"


def test_assignment_api_requires_competition_mode_and_racing_subject(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client = TestClient(app)
    assert client.post("/api/hyrox/venue-config", json=_venue_body()).status_code == 200
    assert client.post("/api/hyrox/register", json={
        "athlete_name": "Alex",
        "rfid_tag_id": "TAG_ALEX",
    }).status_code == 200

    training = client.post("/api/hyrox/assign", json={
        "subject_id": "TAG_ALEX",
        "resource_id": "treadmill-01",
    })

    assert training.status_code == 409
    assert "competition mode" in training.json()["detail"]

    competition = _assignment_venue_body()
    assert client.post("/api/hyrox/venue-config", json=competition).status_code == 200
    assert client.post("/api/hyrox/register", json={
        "athlete_name": "Alex",
        "rfid_tag_id": "TAG_ALEX",
    }).status_code == 200
    assert client.post(
        "/api/hyrox/abandon", json={"subject_id": "TAG_ALEX"}
    ).status_code == 200

    terminal = client.post("/api/hyrox/assign", json={
        "subject_id": "TAG_ALEX",
        "resource_id": "treadmill-01",
    })

    assert terminal.status_code == 409
    assert terminal.json()["detail"] == "Subject TAG_ALEX is not racing"
    assert client.get("/api/hyrox/state").json()["resources"]["treadmill-01"] == "free"


def test_assignment_api_team_tag_and_pre_start_conflict_guards(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    client = TestClient(app)
    assert client.post(
        "/api/hyrox/venue-config", json=_assignment_venue_body()
    ).status_code == 200
    for name, tag in (("One", "TAG_ONE"), ("Two", "TAG_TWO")):
        assert client.post("/api/hyrox/register", json={
            "athlete_name": name,
            "rfid_tag_id": tag,
            "division": "doubles",
            "team_name": "Duo",
        }).status_code == 200
    assert client.post("/api/hyrox/register", json={
        "athlete_name": "Bella",
        "rfid_tag_id": "TAG_BELLA",
    }).status_code == 200

    missing_tag = client.post("/api/hyrox/assign", json={
        "subject_id": "Duo",
        "resource_id": "treadmill-01",
    })
    wrong_tag = client.post("/api/hyrox/assign", json={
        "subject_id": "Duo",
        "resource_id": "treadmill-01",
        "active_tag_id": "TAG_STRANGER",
    })

    assert missing_tag.status_code == 422
    assert wrong_tag.status_code == 422
    assert client.get("/api/hyrox/state").json()["resources"]["treadmill-01"] == "free"

    assigned = client.post("/api/hyrox/assign", json={
        "subject_id": "Duo",
        "resource_id": "treadmill-01",
        "active_tag_id": "TAG_TWO",
    })
    occupied = client.post("/api/hyrox/assign", json={
        "subject_id": "TAG_BELLA",
        "resource_id": "treadmill-01",
    })

    assert assigned.status_code == 200
    assert occupied.status_code == 409
    assert occupied.json()["detail"] == "Resource treadmill-01 is occupied"
    state = client.get("/api/hyrox/state").json()
    subjects = {subject["subject_id"]: subject for subject in state["subjects"]}
    assert subjects["Duo"]["assigned_resource"] == "treadmill-01"
    assert subjects["TAG_BELLA"]["assigned_resource"] is None


def test_abandon_before_activity_finalizes_retrievable_dnf(monkeypatch, tmp_path):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    from hub_server.usecases.hyrox_results_store import HyroxResultsStore

    store = HyroxResultsStore(str(tmp_path / "pre-activity-dnf.db"))
    hyrox_service.attach_results_store(store)
    try:
        client = TestClient(app)
        assert client.post(
            "/api/hyrox/venue-config", json=_venue_body()
        ).status_code == 200
        registration = client.post("/api/hyrox/register", json={
            "athlete_name": "Alex",
            "rfid_tag_id": "TAG_ALEX",
        })
        token = registration.json()["result_token"]
        assert client.post("/api/hyrox/start").status_code == 200

        abandoned = client.post(
            "/api/hyrox/abandon", json={"subject_id": "TAG_ALEX"}
        )

        assert abandoned.status_code == 200
        result_response = client.get(f"/api/hyrox/result/{token}")
        assert result_response.status_code == 200
        result = result_response.json()
        assert isinstance(result["started_at_ms"], int)
        assert result["status"] == "dnf"
        assert result["finished_at_ms"] is None
        assert result["total_time_ms"] is None
        assert result["splits"] == []
    finally:
        hyrox_service.attach_results_store(None)
        store.close()


def test_results_finalized_and_retrievable(monkeypatch, tmp_path):
    # Placed last: configures the shared service, so it must not run before the
    # tests that assume an unconfigured service.
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    from hub_server.usecases.hyrox_results_store import HyroxResultsStore
    hyrox_service.attach_results_store(HyroxResultsStore(str(tmp_path / "r.db")))
    try:
        client = TestClient(app)
        client.post("/api/hyrox/venue-config", json=_venue_body())
        token = client.post("/api/hyrox/register", json={
            "athlete_name": "Alex", "rfid_tag_id": "TAG_ALEX"}).json()["result_token"]
        assert token
        client.post("/api/hyrox/start")

        # Force the athlete through all 16 stages to FINISHED.
        for _ in range(16):
            client.post("/api/hyrox/complete-stage", json={"subject_id": "TAG_ALEX"})

        res = client.get(f"/api/hyrox/result/{token}")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "finished"
        assert body["rank"] == 1
        assert len(body["splits"]) == 16

        race_id = hyrox_service.race_id
        full = client.get(f"/api/hyrox/results/{race_id}")
        assert full.status_code == 200 and len(full.json()["athletes"]) == 1
        csv = client.get(f"/api/hyrox/results/{race_id}/export.csv")
        assert csv.status_code == 200 and "Alex" in csv.text

        assert client.get("/api/hyrox/result/nope").status_code == 404
    finally:
        hyrox_service.attach_results_store(None)


def test_diagnostics_and_races_endpoints_require_admin_and_return_data(monkeypatch, tmp_path):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "secret")
    from hub_server.usecases.hyrox_results_store import HyroxResultsStore

    store = HyroxResultsStore(str(tmp_path / "diag.db"))
    hyrox_service.attach_results_store(store)
    try:
        client = TestClient(app)
        headers = {"X-FitRace-Admin-Token": "secret"}

        # 401 without the admin token.
        assert client.get("/api/hyrox/races").status_code == 401
        assert client.get("/api/hyrox/diagnostics/some-race").status_code == 401

        assert client.post(
            "/api/hyrox/venue-config", json=_venue_body(), headers=headers
        ).status_code == 200
        race_id = hyrox_service.race_id
        client.post("/api/hyrox/register", json={
            "athlete_name": "Alex", "rfid_tag_id": "TAG_ALEX"})
        client.post("/api/hyrox/start", headers=headers)
        for _ in range(16):
            client.post("/api/hyrox/complete-stage", json={"subject_id": "TAG_ALEX"},
                       headers=headers)

        # Seed a diagnostic the same way a rejected sensor event does in
        # production (HyroxAssignmentStore.record_diagnostic -> the durable
        # audit sink wired in HyroxService).
        hyrox_service._store.record_diagnostic(
            "conflict", "treadmill-01", "seeded for test", 123,
        )

        diagnostics = client.get(f"/api/hyrox/diagnostics/{race_id}", headers=headers)
        assert diagnostics.status_code == 200
        assert any(d["detail"] == "seeded for test" for d in diagnostics.json())

        races = client.get("/api/hyrox/races", headers=headers)
        assert races.status_code == 200
        by_id = {r["race_id"]: r for r in races.json()}
        assert race_id in by_id
        assert by_id[race_id]["venue_id"] == "hq"
        assert by_id[race_id]["finalized_count"] == 1
    finally:
        hyrox_service.attach_results_store(None)


def test_restart_recovers_race_state_from_disk(monkeypatch, tmp_path):
    # Simulates the Phase 7 recovery flow: the running Hub persists to a state
    # file as it handles requests, and a freshly booted HyroxService loading
    # that same file (as hub_server/main.py does at startup) picks up where
    # the previous process left off.
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    from hub_server.usecases.hyrox_service import HyroxService

    state_path = tmp_path / "state.json"
    assert hyrox_service.load_snapshot(str(state_path)) is False  # nothing yet
    try:
        client = TestClient(app)
        assert client.post(
            "/api/hyrox/venue-config", json=_assignment_venue_body()
        ).status_code == 200
        registration = client.post("/api/hyrox/register", json={
            "athlete_name": "Alex", "rfid_tag_id": "TAG_ALEX"})
        token = registration.json()["result_token"]
        assert client.post("/api/hyrox/start").status_code == 200
        assert client.post("/api/hyrox/assign", json={
            "subject_id": "TAG_ALEX", "resource_id": "treadmill-01",
        }).status_code == 200

        restarted = HyroxService()
        assert restarted.load_snapshot(str(state_path)) is True
        assert restarted.recovered is True
        assert restarted.race_id == hyrox_service.race_id
        assert restarted.token_for("TAG_ALEX") == token
        state = restarted.get_state()
        assert state["recovered"] is True
        assert state["subjects"][0]["assigned_resource"] == "treadmill-01"
    finally:
        # Disable persistence again so later tests in this module (which share
        # the hyrox_service singleton) do not keep writing to this tmp_path.
        hyrox_service._state_path = None
