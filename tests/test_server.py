from aiosyslogd.db import BaseDatabase
from aiosyslogd.server import SyslogUDPServer, get_db_driver
from datetime import datetime
from loguru import logger
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import pytest
import pytest_asyncio
import sys


@pytest.fixture(autouse=True)
def setup_logger_for_server(capsys):
    """Fixture to configure and reset the logger for each test."""
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    yield
    logger.remove()


def create_test_datagram(
    message: str, priority: int = 34, ts: str = "2025-06-11T12:00:00.000Z"
) -> bytes:
    """Creates a sample RFC5424 syslog message for testing."""
    return f"<{priority}>1 {ts} testhost testapp 1234 - - {message}".encode(
        "utf-8"
    )


@pytest.fixture
def mock_db():
    db = AsyncMock(spec=BaseDatabase)
    db.connect = AsyncMock()
    db.write_batch = AsyncMock()
    db.close = AsyncMock()
    return db


@pytest_asyncio.fixture
async def server(mock_db):
    with patch("aiosyslogd.server.get_db_driver", return_value=mock_db):
        with patch("aiosyslogd.server.BATCH_SIZE", 1):
            server = await SyslogUDPServer.create(host="127.0.0.1", port=5141)
    yield server
    try:
        if server._db_writer_task and not server._db_writer_task.done():
            server._db_writer_task.cancel()
        await server.shutdown()
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_server_creation(server, mock_db):
    assert server.host == "127.0.0.1"
    assert server.port == 5141
    assert server.db == mock_db
    assert server._message_queue.empty()
    mock_db.connect.assert_called_once()


@pytest.mark.asyncio
async def test_server_creation_debug_mode(mock_db, capsys):
    with patch("aiosyslogd.server.DEBUG", True):
        server = await SyslogUDPServer.create(host="127.0.0.1", port=5141)
        captured = capsys.readouterr()
        assert "Debug mode is ON." in captured.err
        try:
            await server.shutdown()
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_error_received(server, capsys):
    test_exc = ValueError("Test Error")
    server.error_received(test_exc)
    captured = capsys.readouterr()
    assert "Error received: Test Error" in captured.err


@pytest.mark.asyncio
async def test_connection_lost(server, capsys):
    test_exc = ConnectionAbortedError("Connection lost unexpectedly")
    server.connection_lost(test_exc)
    captured = capsys.readouterr()
    assert "Connection lost: Connection lost unexpectedly" in captured.err


@pytest.mark.asyncio
async def test_database_writer_exception(server, mock_db, capsys):
    server.connection_made(MagicMock())
    with patch.object(
        server, "process_datagram", side_effect=ValueError("Processing failed")
    ):
        server.datagram_received(
            create_test_datagram("bad log"), ("localhost", 123)
        )
        await asyncio.sleep(0.01)
        captured = capsys.readouterr()
        assert "Error in database writer" in captured.err
        assert "Processing failed" in captured.err
        mock_db.write_batch.assert_not_called()


@pytest.mark.asyncio
async def test_process_datagram_log_dump_on(server, capsys):
    test_data = create_test_datagram("Log dump test")
    addr = ("192.168.1.1", 12345)
    with patch("aiosyslogd.server.LOG_DUMP", True):
        server.process_datagram(test_data, addr, datetime.now())
        captured = capsys.readouterr()
        assert "FROM 192.168.1.1:" in captured.err
        assert "Log dump test" in captured.err


@pytest.mark.asyncio
async def test_process_datagram_invalid_encoding(server, capsys):
    test_data = b"\xff\xfe"
    addr = ("192.168.1.1", 12345)
    params = server.process_datagram(test_data, addr, datetime.now())
    captured = capsys.readouterr()
    assert params is None
    assert "Cannot decode message" in captured.err


@pytest.mark.asyncio
async def test_debug_mode_invalid_datagram(server, capsys):
    with patch("aiosyslogd.server.DEBUG", True):
        test_data = b"this is not a syslog message"
        addr = ("192.168.1.1", 12345)
        params = server.process_datagram(test_data, addr, datetime.now())
        captured = capsys.readouterr()
        assert (
            "Failed to parse as RFC-5424: this is not a syslog message"
            in captured.err
        )
        assert params is not None
        assert params["Message"] == "this is not a syslog message"


def test_get_db_driver_injection_attempt(capsys):
    malicious_driver_name = "../../../../os"
    with patch("aiosyslogd.server.DB_DRIVER", malicious_driver_name):
        with pytest.raises(SystemExit):
            get_db_driver()
    captured = capsys.readouterr()
    assert "Invalid database driver" in captured.err
    assert f"'{malicious_driver_name}'" in captured.err


@pytest.mark.asyncio
async def test_process_datagram_invalid_encoding_logs_address(server, capsys):
    test_data = b"\xff\xfe"
    addr = ("192.168.1.1", 12345)
    server.process_datagram(test_data, addr, datetime.now())
    captured = capsys.readouterr()
    assert "Cannot decode message from 192.168.1.1" in captured.err
