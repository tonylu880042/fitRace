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
