import asyncio
import logging

import pytest

from hub_server import main as hub_main


def test_main_reraises_unexpected_exception_with_traceback(monkeypatch, caplog):
    error = RuntimeError("startup failed")

    def fail_run(coro):
        coro.close()
        raise error

    monkeypatch.setattr(asyncio, "run", fail_run)

    with caplog.at_level(logging.CRITICAL, logger="hub_server.main"):
        with pytest.raises(RuntimeError, match="startup failed"):
            hub_main.main()

    record = next(
        record for record in caplog.records if record.name == "hub_server.main"
    )
    assert record.exc_info is not None
    assert "Hub Server crashed" in record.message


def test_main_handles_keyboard_interrupt_without_reraising(monkeypatch, caplog):
    def interrupt_run(coro):
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", interrupt_run)

    with caplog.at_level(logging.INFO, logger="hub_server.main"):
        hub_main.main()

    assert any(
        record.name == "hub_server.main"
        and record.levelno == logging.INFO
        and "Hub Server stopped by user interrupt" in record.message
        for record in caplog.records
    )
