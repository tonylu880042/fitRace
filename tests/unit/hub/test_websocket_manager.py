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


class SignalingWebSocket(FakeWebSocket):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.received = asyncio.Event()

    async def send_json(self, message):
        await super().send_json(message)
        self.received.set()


@pytest.mark.asyncio
async def test_hung_connection_ahead_does_not_delay_healthy_connection():
    manager = WebSocketManager(send_timeout_sec=0.5)
    hung = FakeWebSocket(gate=asyncio.Event())
    healthy = SignalingWebSocket()
    manager.active_connections = [hung, healthy]

    broadcast_task = asyncio.create_task(manager.broadcast({"type": "tick"}))
    try:
        await asyncio.sleep(0.15)
        assert healthy.received.is_set(), "healthy client waited behind the hung one"
        assert healthy.messages == [{"type": "tick"}]
    finally:
        await broadcast_task


@pytest.mark.asyncio
async def test_hung_connection_is_removed_after_broadcast_and_healthy_remains():
    manager = WebSocketManager(send_timeout_sec=0.05)
    hung = FakeWebSocket(gate=asyncio.Event())
    healthy = FakeWebSocket()
    manager.active_connections = [hung, healthy]

    await manager.broadcast({"type": "tick"})

    assert manager.active_connections == [healthy]
    assert healthy.messages == [{"type": "tick"}]


@pytest.mark.asyncio
async def test_failed_connection_is_removed_and_others_still_receive():
    manager = WebSocketManager()
    healthy_before = FakeWebSocket()
    failed = FakeWebSocket(error=RuntimeError("gone"))
    healthy_after = FakeWebSocket()
    manager.active_connections = [healthy_before, failed, healthy_after]

    await manager.broadcast({"type": "tick"})

    assert healthy_before.messages == [{"type": "tick"}]
    assert healthy_after.messages == [{"type": "tick"}]
    assert manager.active_connections == [healthy_before, healthy_after]


@pytest.mark.asyncio
async def test_cancelling_broadcast_with_several_hung_connections_raises_cancelled():
    manager = WebSocketManager(send_timeout_sec=5)
    hung_a = FakeWebSocket(gate=asyncio.Event())
    hung_b = FakeWebSocket(gate=asyncio.Event())
    healthy = FakeWebSocket()
    manager.active_connections = [hung_a, hung_b, healthy]

    broadcast_task = asyncio.create_task(manager.broadcast({"type": "tick"}))
    await hung_a.send_started.wait()
    await hung_b.send_started.wait()
    broadcast_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await broadcast_task

    assert manager.active_connections == [hung_a, hung_b, healthy]
