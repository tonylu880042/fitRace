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
