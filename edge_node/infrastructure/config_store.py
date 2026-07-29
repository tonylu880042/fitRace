"""Shared Edge Node config.json load/save.

Plain module: no FastAPI import. `edge_node/infrastructure/fastapi/app.py`
(the web setup service) and the runtime (`edge_node/main.py`,
`edge_node/usecases/antenna_ftms_manager.py`) both need atomic config.json
access, but the runtime must never import FastAPI -- Clean Architecture
requires usecases to stay independent of any web framework.

HTTP error translation for invalid config on disk stays in app.py: this
module raises the plain underlying exception (`json.JSONDecodeError` or
`ValueError`, which pydantic's `ValidationError` subclasses) rather than an
`HTTPException`.
"""

import json
import os
import tempfile
from pathlib import Path

from edge_node.domain.models import EdgeNodeConfig

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def load_edge_config(config_path: Path | None = None) -> EdgeNodeConfig:
    path = config_path if config_path is not None else CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return EdgeNodeConfig.model_validate(json.load(f))
    except FileNotFoundError:
        return EdgeNodeConfig(
            node_id="fitrace-edge", antenna_protocol_version="unknown"
        )


def save_edge_config(config: EdgeNodeConfig, config_path: Path | None = None) -> None:
    path = config_path if config_path is not None else CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as config_file:
            temp_path = Path(config_file.name)
            json.dump(
                config.model_dump(),
                config_file,
                indent=2,
                ensure_ascii=False,
            )
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
