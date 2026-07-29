import pytest

from edge_node.infrastructure.antenna.command_runner import (
    AntennaCommandRequest,
    _build_commands,
)


def test_build_commands_connect_add_with_single_mac():
    request = AntennaCommandRequest(
        port="/dev/fake0", command="connect_add", macs=["AA:BB:CC:DD:EE:01"]
    )

    assert _build_commands(request) == ["CONNECT_ADD:AA:BB:CC:DD:EE:01;\r\n"]


def test_build_commands_connect_add_requires_exactly_one_mac():
    with pytest.raises(ValueError):
        _build_commands(
            AntennaCommandRequest(port="/dev/fake0", command="connect_add", macs=[])
        )
    with pytest.raises(ValueError):
        _build_commands(
            AntennaCommandRequest(
                port="/dev/fake0",
                command="connect_add",
                macs=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"],
            )
        )


def test_build_commands_disconnect_with_single_mac():
    request = AntennaCommandRequest(
        port="/dev/fake0", command="disconnect", macs=["AA:BB:CC:DD:EE:01"]
    )

    assert _build_commands(request) == ["DISCONNECT:AA:BB:CC:DD:EE:01;\r\n"]


def test_build_commands_disconnect_requires_exactly_one_mac():
    with pytest.raises(ValueError):
        _build_commands(
            AntennaCommandRequest(port="/dev/fake0", command="disconnect", macs=[])
        )
    with pytest.raises(ValueError):
        _build_commands(
            AntennaCommandRequest(
                port="/dev/fake0",
                command="disconnect",
                macs=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"],
            )
        )
