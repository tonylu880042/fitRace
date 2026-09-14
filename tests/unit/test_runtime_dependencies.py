"""Guard against the hub shipping without a WebSocket implementation.

``pyproject.toml`` used to list plain ``uvicorn==0.49.0`` (not the
``uvicorn[standard]`` extra) and declared no WebSocket library of its own.
On a fresh Raspberry Pi venv built strictly from the declared dependencies,
uvicorn logs ``No supported WebSocket library detected`` at startup and
answers every ``GET /ws/dashboard`` upgrade request with a 404 instead of
switching protocols. The venue dashboard then never receives live progress
(relay leg changes, handoff cue, leaderboard updates) even though the hub
process itself looks healthy. This only went unnoticed because the
developer machine happened to have ``websockets==16.0`` installed
transitively by dev tooling, masking the missing declared dependency. This
test fails whenever ``[project].dependencies`` stops declaring a websockets
requirement.
"""

import tomllib
from pathlib import Path

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_dependencies_declare_websockets_for_hub_dashboard():
    with PYPROJECT_PATH.open("rb") as f:
        pyproject = tomllib.load(f)

    dependencies = pyproject["project"]["dependencies"]

    assert any(
        dep == "websockets"
        or dep.startswith(("websockets==", "websockets>=", "websockets~="))
        for dep in dependencies
    ), "pyproject.toml [project].dependencies must declare websockets, or the hub cannot serve /ws/dashboard"
