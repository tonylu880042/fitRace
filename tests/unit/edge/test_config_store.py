import json

import pytest

from edge_node.domain.models import EdgeNodeConfig
from edge_node.infrastructure import config_store


def test_load_edge_config_returns_default_when_file_missing(tmp_path):
    config = config_store.load_edge_config(tmp_path / "does-not-exist.json")

    assert config.node_id == "fitrace-edge"


def test_save_then_load_round_trips_equipment_bindings(tmp_path):
    config_path = tmp_path / "config.json"
    config = EdgeNodeConfig(node_id="edge-01")

    config_store.save_edge_config(config, config_path)
    reloaded = config_store.load_edge_config(config_path)

    assert reloaded.node_id == "edge-01"
    assert config_path.exists()


def test_load_edge_config_raises_plain_exception_on_invalid_json(tmp_path):
    # HTTP translation of this failure belongs in app.py, not here -- the
    # store must stay free of any FastAPI import.
    config_path = tmp_path / "config.json"
    config_path.write_text("not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        config_store.load_edge_config(config_path)


def test_config_store_module_does_not_import_fastapi():
    # Clean Architecture guard: usecases/main.py depend on this module and
    # must never end up importing FastAPI transitively through it.
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(config_store))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any(name.startswith("fastapi") for name in imported_modules)
