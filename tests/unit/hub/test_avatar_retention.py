"""R2: a photo is kept only while it is needed -- the athlete is registered
for the current/next run, or still ranks in the event's top 10. Everything
else is deleted, and standings never hand out a URL whose file is gone."""

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.usecases.avatar_retention import avatar_id_from_url, partition_avatars
from hub_server.usecases.avatar_store import AvatarStore
from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery

WEBP = b"RIFF\x04\x00\x00\x00WEBPxxxx"
A, B, C = "a" * 32, "b" * 32, "c" * 32


def test_avatar_id_from_url_accepts_only_our_urls():
    assert avatar_id_from_url(f"/api/avatars/{A}.webp") == A
    for bad in [
        None,
        "",
        "/static/x.webp",
        f"/api/avatars/{A}.png",
        "/api/avatars/../x.webp",
        f"/api/avatars/{'A' * 32}.webp",
        5,
    ]:
        assert avatar_id_from_url(bad) is None


def test_partition_splits_stored_ids_exactly_once():
    kept, doomed = partition_avatars([A, B, C], keep={A, C, "z" * 32})
    assert kept == [A, C] and doomed == [B]
    assert set(kept) | set(doomed) == {A, B, C}
    assert not set(kept) & set(doomed)


def test_partition_with_nothing_to_keep_dooms_everything():
    assert partition_avatars([A, B], keep=set()) == ([], [A, B])


def test_store_lists_and_deletes_by_id(tmp_path):
    store = AvatarStore(tmp_path)
    one, two = store.save(WEBP), store.save(WEBP)
    (tmp_path / "notes.txt").write_text("not an avatar")
    (tmp_path / "short.webp").write_bytes(WEBP)

    assert sorted(store.list_ids()) == sorted([one, two])
    assert store.delete(one) is True
    assert store.path_for(one) is None and store.path_for(two) is not None
    assert store.delete(one) is False
    assert store.delete("../" + "a" * 29) is False
    assert (tmp_path / "notes.txt").exists()


def _row(n, name, distance, avatar_url):
    return {
        "node_id": f"node-{n:02d}",
        "athlete_name": name,
        "is_registered_name": True,
        "station_number": n,
        "team_name": None,
        "division": None,
        "avatar_url": avatar_url,
        "distance_m": distance,
        "elapsed_time_ms": 180000,
        "progress_percent": 100.0,
        "calories": 0,
        "max_power_watts": 0,
        "finished_time_ms": None,
    }


def _store_with_rows(tmp_path, rows):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        {
            "state": "STOPPED",
            "config": {
                "race_type": "time",
                "competition_mode": "individual",
                "team_scoring_policy": None,
                "target_value": 0,
                "duration_sec": 180,
                "relay_legs": None,
            },
            "start_time_epoch_ms": 1000,
            "end_time_epoch_ms": 181000,
            "leaderboard": {r["node_id"]: r for r in rows},
            "team_leaderboard": None,
        }
    )
    return store


def test_standings_null_out_urls_whose_file_was_deleted(tmp_path):
    rows = [
        _row(1, "Has", 500, f"/api/avatars/{A}.webp"),
        _row(2, "Gone", 400, f"/api/avatars/{B}.webp"),
    ]
    query = RaceResultsQuery(
        _store_with_rows(tmp_path, rows),
        avatar_exists=lambda url: avatar_id_from_url(url) == A,
    )

    got = {
        r["athlete_name"]: r["avatar_url"]
        for r in query.get_standings()["sections"][0]["rows"]
    }

    assert got == {"Has": f"/api/avatars/{A}.webp", "Gone": None}


@pytest.fixture
def client():
    c = TestClient(hub_app.app)
    c.post("/api/race/reset")
    yield c
    hub_app.challenge_pending_registrations.clear()
    c.post("/api/race/reset")


def test_prune_keeps_top_10_registered_and_queued_and_deletes_the_rest(
    client, tmp_path, monkeypatch
):
    store = hub_app.avatar_store
    ids = [store.save(WEBP) for _ in range(12)]  # ranks 1..12
    rows = [
        _row(i + 1, f"R{i + 1}", 1000 - i, f"/api/avatars/{ids[i]}.webp")
        for i in range(12)
    ]
    monkeypatch.setattr(
        hub_app,
        "race_results_query",
        RaceResultsQuery(_store_with_rows(tmp_path, rows)),
    )
    registered = store.save(WEBP)
    queued = store.save(WEBP)
    orphan = store.save(WEBP)
    hub_app.race_manager.register_athlete(1, "Now", avatar_id=registered)
    hub_app.challenge_pending_registrations.append(
        {"station_number": 2, "athlete_name": "Q", "avatar_id": queued}
    )

    removed = hub_app.prune_unneeded_avatars()

    assert sorted(store.list_ids()) == sorted(ids[:10] + [registered, queued])
    assert removed == 3  # ranks 11, 12 and the orphan
    assert store.path_for(orphan) is None and store.path_for(ids[10]) is None


def test_reset_prunes_photos_nobody_needs_any_more(client):
    store = hub_app.avatar_store
    stale = store.save(WEBP)

    client.post("/api/race/reset")

    assert store.path_for(stale) is None


def test_registered_photo_survives_until_the_run_is_reset(client):
    import base64

    tiny = "data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4H"
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "ret-1"})
    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Amy", "avatar_base64": tiny},
    )
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    rows = client.get("/api/race/state").json()["leaderboard"]
    url = next(r["avatar_url"] for r in rows.values() if r["station_number"] == 1)

    assert client.get(url).status_code == 200
    assert client.get(url).content == base64.b64decode(tiny.split(",", 1)[1])

    client.post("/api/race/reset")
    assert client.get(url).status_code == 404  # never ranked, run over


def _store_race(store, rows, start, race_type="time", **config):
    cfg = {
        "race_type": race_type,
        "competition_mode": "individual",
        "team_scoring_policy": None,
        "target_value": config.get("target_value", 0),
        "duration_sec": config.get("duration_sec", 180),
        "relay_legs": None,
    }
    store.save_finished_snapshot(
        {
            "state": "STOPPED",
            "config": cfg,
            "start_time_epoch_ms": start,
            "end_time_epoch_ms": start + 1000,
            "leaderboard": {r["node_id"]: r for r in rows},
            "team_leaderboard": None,
        }
    )


def test_other_category_race_does_not_make_challenge_photos_disposable(
    client, tmp_path, monkeypatch
):
    store = hub_app.avatar_store
    result_store = RaceResultStore(tmp_path / "multi.jsonl")
    challenge = [store.save(WEBP) for _ in range(12)]
    _store_race(
        result_store,
        [
            _row(i + 1, f"C{i + 1}", 1000 - i, f"/api/avatars/{challenge[i]}.webp")
            for i in range(12)
        ],
        start=1000,
    )
    other = store.save(WEBP)
    _store_race(
        result_store,
        [
            {
                **_row(1, "Tester", 500, f"/api/avatars/{other}.webp"),
                "finished_time_ms": 60000,
            }
        ],
        start=5000,
        race_type="distance",
        target_value=500,
        duration_sec=0,
    )
    monkeypatch.setattr(hub_app, "race_results_query", RaceResultsQuery(result_store))

    hub_app.prune_unneeded_avatars()

    kept = set(store.list_ids())
    assert set(challenge[:10]) <= kept  # challenge top 10 survive
    assert other in kept  # so does the other category's winner
    assert challenge[10] not in kept and challenge[11] not in kept  # 11th/12th still go
