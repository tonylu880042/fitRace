import asyncio

import pytest

from hub_server.adapters.websocket_manager import WebSocketManager


class FakeWebSocket:
    def __init__(self, *, error=None, gate=None):
        self.error = error
        self.gate = gate
        self.send_started = asyncio.Event()
        self.messages = []

    async def send_json(self, message):
        self.send_started.set()
        if self.error is not None:
            raise self.error
        if self.gate is not None:
            await self.gate.wait()
        self.messages.append(message)


@pytest.mark.asyncio
async def test_broadcast_sends_message_to_each_healthy_connection():
    manager = WebSocketManager()
    first = FakeWebSocket()
    second = FakeWebSocket()
    manager.active_connections = [first, second]

    message = {"type": "race_state", "state": "RUNNING"}
    await manager.broadcast(message)

    assert first.messages == [message]
    assert second.messages == [message]
    assert manager.active_connections == [first, second]


@pytest.mark.asyncio
async def test_broadcast_removes_multiple_failed_connections_without_skipping_healthy_ones():
    manager = WebSocketManager()
    failed_first = FakeWebSocket(error=RuntimeError("first disconnected"))
    healthy_first = FakeWebSocket()
    failed_second = FakeWebSocket(error=RuntimeError("second disconnected"))
    healthy_second = FakeWebSocket()
    manager.active_connections = [
        failed_first,
        healthy_first,
        failed_second,
        healthy_second,
    ]

    await manager.broadcast({"type": "tick"})

    assert healthy_first.messages == [{"type": "tick"}]
    assert healthy_second.messages == [{"type": "tick"}]
    assert manager.active_connections == [healthy_first, healthy_second]


@pytest.mark.asyncio
async def test_broadcast_times_out_slow_connection_then_reaches_healthy_connection():
    manager = WebSocketManager(send_timeout_sec=0.01)
    slow = FakeWebSocket(gate=asyncio.Event())
    healthy = FakeWebSocket()
    manager.active_connections = [slow, healthy]

    await asyncio.wait_for(manager.broadcast({"type": "tick"}), timeout=0.2)

    assert slow.messages == []
    assert healthy.messages == [{"type": "tick"}]
    assert manager.active_connections == [healthy]


@pytest.mark.asyncio
async def test_broadcast_propagates_cancellation_from_a_slow_connection():
    manager = WebSocketManager(send_timeout_sec=1)
    slow = FakeWebSocket(gate=asyncio.Event())
    manager.active_connections = [slow]

    broadcast_task = asyncio.create_task(manager.broadcast({"type": "tick"}))
    await slow.send_started.wait()
    broadcast_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await broadcast_task

    assert manager.active_connections == [slow]
