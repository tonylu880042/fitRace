"""Shared logic for pushing the antenna boards back to their configured
equipment lists.

Both the plain "CONNECT configured devices" button (POST
/api/antenna/reconnect-configured) and the pairing-session flow's "restore
reality" step after a confirm/cancel (see edge_node/usecases/pairing_session.py)
need to reissue exactly the same per-channel DISCONNECT/CONNECT/REPORT
sequence, so the logic lives here once instead of being duplicated between
the FastAPI endpoint and the pairing session service.
"""

from edge_node.domain.models import EdgeNodeConfig
from edge_node.infrastructure.antenna.command_runner import AntennaCommandRequest


def reconnect_configured_devices(
    config: EdgeNodeConfig,
    runner,
    *,
    disconnect_first: bool = False,
    timeout_sec: float = 5.0,
    report_interval_ms: int = 250,
) -> dict:
    """Reissue CONNECT+REPORT for every channel's configured equipment
    bindings via `runner` (an AntennaCommandRunner or compatible fake).

    Raises ValueError if there are no configured targets at all and
    `disconnect_first` is False (nothing useful to do). When
    `disconnect_first` is True, every channel is sent DISCONNECT:ALL first
    regardless of whether it has any configured targets, since the firmware
    only supports clearing its whole list, not a partial one.
    """
    channels_by_id = {channel.id: channel for channel in config.antenna_channels}
    targets_by_channel: dict[str, list[str]] = {}
    for binding in config.equipment_bindings:
        if not binding.antenna_channel:
            continue
        if binding.antenna_channel not in channels_by_id:
            continue
        targets_by_channel.setdefault(binding.antenna_channel, []).append(
            binding.ble_target
        )

    if not targets_by_channel and not disconnect_first:
        raise ValueError("No configured antenna targets found")

    if disconnect_first:
        # clear every board's link/target list so removed bindings actually
        # free their connection slot (firmware only has ALL)
        for channel in config.antenna_channels:
            runner.run(
                AntennaCommandRequest(
                    port=channel.port,
                    baudrate=channel.baudrate,
                    rtscts=channel.rtscts,
                    command="disconnect_all",
                    timeout_sec=timeout_sec,
                )
            )

    results = []
    for channel_id, macs in targets_by_channel.items():
        channel = channels_by_id[channel_id]
        connect_result = runner.run(
            AntennaCommandRequest(
                port=channel.port,
                baudrate=channel.baudrate,
                rtscts=channel.rtscts,
                command="connect",
                timeout_sec=timeout_sec,
                macs=macs,
            )
        )
        report_result = runner.run(
            AntennaCommandRequest(
                port=channel.port,
                baudrate=channel.baudrate,
                rtscts=channel.rtscts,
                command="report",
                timeout_sec=timeout_sec,
                report_interval_ms=report_interval_ms,
            )
        )
        results.append(
            {
                "channel_id": channel_id,
                "port": channel.port,
                "macs": macs,
                "connect": connect_result,
                "report": report_result,
            }
        )

    return {"status": "reconnected", "channels": results}


def reconnect_single_device(
    config: EdgeNodeConfig,
    runner,
    node_id: str,
    *,
    timeout_sec: float = 5.0,
    report_interval_ms: int = 250,
) -> dict:
    """Kick one binding back into connecting, leaving its channel-mates alone.

    The runtime watchdog deliberately waits when a board holds the right
    target list but has not linked every device yet -- resending CONNECT
    would restart the firmware's own auto-reconnect. This is the operator
    override for that wait, so it uses the single-MAC commands
    (DISCONNECT <mac> then CONNECT_ADD <mac>): CONNECT would replace the
    channel's whole target list and DISCONNECT:ALL would clear it, either
    of which drops the other machines on the same board.

    Raises ValueError before touching the UART when the node is unknown, has
    no antenna channel, or names a channel this node does not have.
    """
    binding = next(
        (b for b in config.equipment_bindings if b.node_id == node_id),
        None,
    )
    if binding is None:
        raise ValueError(f"Unknown node_id: {node_id}")
    if not binding.antenna_channel:
        raise ValueError(f"{node_id} has no antenna channel assigned")
    if not binding.ble_target:
        raise ValueError(f"{node_id} has no BLE target")

    channel = next(
        (c for c in config.antenna_channels if c.id == binding.antenna_channel),
        None,
    )
    if channel is None:
        raise ValueError(f"Unknown antenna channel: {binding.antenna_channel}")

    def _run(command: str, **extra):
        return runner.run(
            AntennaCommandRequest(
                port=channel.port,
                baudrate=channel.baudrate,
                rtscts=channel.rtscts,
                command=command,
                timeout_sec=timeout_sec,
                **extra,
            )
        )

    disconnect_result = _run("disconnect", macs=[binding.ble_target])
    connect_result = _run("connect_add", macs=[binding.ble_target])
    report_result = _run("report", report_interval_ms=report_interval_ms)

    return {
        "status": "reconnecting",
        "node_id": node_id,
        "channel_id": channel.id,
        "port": channel.port,
        "mac": binding.ble_target,
        "disconnect": disconnect_result,
        "connect_add": connect_result,
        "report": report_result,
    }
