from pathlib import Path


def test_fastapi_and_mqtt_adapters_do_not_access_race_manager_private_fields():
    root = Path(__file__).resolve().parents[3]
    files = [
        root / "hub_server" / "infrastructure" / "fastapi" / "app.py",
        root / "hub_server" / "adapters" / "mqtt_subscriber.py",
    ]

    for file_path in files:
        source = file_path.read_text()
        assert "race_manager._" not in source
        assert "_race_manager._" not in source

