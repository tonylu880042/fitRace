import asyncio
from typing import List
from fastapi import WebSocket


class WebSocketManager:
    def __init__(self, send_timeout_sec: float = 5.0):
        self.active_connections: List[WebSocket] = []
        self.send_timeout_sec = send_timeout_sec

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def _send(self, connection: WebSocket, message: dict) -> bool:
        try:
            await asyncio.wait_for(
                connection.send_json(message), timeout=self.send_timeout_sec
            )
        except Exception:
            # Failed or timed out (client disconnected or hung); the caller
            # drops it. CancelledError is a BaseException and still propagates.
            return False
        return True

    async def broadcast(self, message: dict):
        # Send to every client concurrently so one hung client cannot delay
        # the others (e.g. the projector dashboard).
        connections = list(self.active_connections)
        results = await asyncio.gather(
            *(self._send(connection, message) for connection in connections)
        )
        for connection, ok in zip(connections, results):
            if not ok:
                self.disconnect(connection)
