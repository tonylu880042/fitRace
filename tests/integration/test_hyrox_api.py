"""Integration tests for the resource-aware Hyrox HTTP API (Phase 6a)."""

import pytest
from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app, hyrox_service


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


def test_hyrox_endpoints_404_when_disabled(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "0")
    client = TestClient(app)
    assert client.get("/api/hyrox/state").status_code == 404
    assert client.post("/api/hyrox/venue-config", json=_venue_body()).status_code == 404


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
