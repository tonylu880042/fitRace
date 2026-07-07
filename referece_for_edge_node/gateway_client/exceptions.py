class GatewayError(Exception):
    """所有 Gateway 相關例外的基底。"""


class GatewayConnectionError(GatewayError):
    """Serial port 無法開啟或已斷線。"""


class GatewayTimeoutError(GatewayError):
    """等待 Central Board 回應超時。"""


class GatewayParseError(GatewayError):
    """無法解析 Central Board 回傳的訊息。"""


class GatewayCommandError(GatewayError):
    """Central Board 回傳 ERROR 回應。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Gateway command error: {reason}")
