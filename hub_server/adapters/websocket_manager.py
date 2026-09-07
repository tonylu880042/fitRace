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

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await asyncio.wait_for(
                    connection.send_json(message), timeout=self.send_timeout_sec
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # If sending fails or times out (e.g., client disconnected or
                # hung), we disconnect them without affecting later ones.
                self.disconnect(connection)
