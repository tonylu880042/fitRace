"""
logging_setup.py — 共用 logging 設定

- 畫面是否顯示由 config.LOG_SHOW_ON_SCREEN 控制。
- 不論畫面是否顯示，log 一律寫入 config.LOG_DIR，
  檔名為建立當下的日期時間，每 config.LOG_ROTATE_HOURS 小時產生一份新檔。
- 每行訊息最前面標註 [UART]（原始封包 TX/RX）或 [LOG]（一般日誌）。
"""
import logging
import logging.handlers
import os
from datetime import datetime

import config

_FMT = "[%(tag)s] %(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class TaggedFormatter(logging.Formatter):
    """未指定 tag 的紀錄視為一般日誌，補上 [LOG]；UART TX/RX 由呼叫端傳入 extra={"tag": "UART"}。"""
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "tag"):
            record.tag = "LOG"
        return super().format(record)


class HourlyFileHandler(logging.handlers.TimedRotatingFileHandler):
    """每次輪轉都另開一個以當下日期時間命名的新檔，而非沿用固定檔名再改名。"""
    def __init__(self, log_dir: str, interval_hours: int):
        os.makedirs(log_dir, exist_ok=True)
        self._log_dir = log_dir
        super().__init__(self._make_filename(), when="H", interval=interval_hours,
                          backupCount=0, encoding="utf-8")

    def _make_filename(self) -> str:
        return os.path.join(self._log_dir, datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = self._make_filename()
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(datetime.now().timestamp()))


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    if config.LOG_SHOW_ON_SCREEN:
        console = logging.StreamHandler()
        console.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
        console.setFormatter(TaggedFormatter(fmt=_FMT, datefmt=_DATEFMT))
        root.addHandler(console)

    file_handler = HourlyFileHandler(config.LOG_DIR, config.LOG_ROTATE_HOURS)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(TaggedFormatter(fmt=_FMT, datefmt=_DATEFMT))
    root.addHandler(file_handler)
