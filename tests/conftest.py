import pytest

from hub_server.usecases.race_result_store import RaceResultStore


@pytest.fixture(autouse=True)
def isolate_hub_race_results(monkeypatch, tmp_path):
    import hub_server.infrastructure.fastapi.app as hub_app

    monkeypatch.setattr(
        hub_app,
        "race_result_store",
        RaceResultStore(tmp_path / "race_results.jsonl"),
    )


@pytest.fixture(autouse=True)
def isolate_hub_avatars(monkeypatch, tmp_path):
    import hub_server.infrastructure.fastapi.app as hub_app
    from hub_server.usecases.avatar_store import AvatarStore

    monkeypatch.setattr(hub_app, "avatar_store", AvatarStore(tmp_path / "avatars"))
