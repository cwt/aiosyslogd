import pytest
import pytest_asyncio
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch
from aiosyslogd.server import SyslogUDPServer
from aiosyslogd.db import BaseDatabase


# Helper function to create a mock datagram
def create_test_datagram(message: str, priority: int = 34) -> bytes:
    """Creates a sample RFC5424 syslog message for testing."""
    return f"<{priority}>1 2025-06-11T12:00:00.000Z testhost testapp 1234 - - {message}".encode(
        "utf-8"
    )


# Pytest fixtures
@pytest.fixture
def mock_db():
    """Provides a mocked BaseDatabase instance."""
    db = AsyncMock(spec=BaseDatabase)
    db.connect = AsyncMock()
    db.write_batch = AsyncMock()
    db.close = AsyncMock()
    return db


@pytest_asyncio.fixture
async def server(mock_db):
    """Provides a SyslogUDPServer instance with a mocked database."""
    with patch("aiosyslogd.server.get_db_driver", return_value=mock_db):
        with patch(
            "aiosyslogd.server.BATCH_SIZE", 1
        ):  # Force small batch size for testing
            server = await SyslogUDPServer.create(host="127.0.0.1", port=5141)
    yield server
    try:
        await server.shutdown()
    except asyncio.CancelledError:
        pass  # Expected during shutdown


# Test cases
@pytest.mark.asyncio
async def test_server_creation(server, mock_db):
    """Tests that the server is created and initialized correctly."""
    # Assert
    assert server.host == "127.0.0.1"
    assert server.port == 5141
    assert server.db == mock_db
    assert server._message_queue.empty()
    mock_db.connect.assert_called_once()


@pytest.mark.asyncio
async def test_datagram_received(server):
    """Tests that datagram_received queues messages correctly."""
    # Arrange
    test_data = create_test_datagram("Test message")
    addr = ("192.168.1.1", 12345)

    # Act
    server.datagram_received(test_data, addr)

    # Assert
    assert server._message_queue.qsize() == 1
    queued_item = await server._message_queue.get()
    assert queued_item[0] == test_data
    assert queued_item[1] == addr
    assert isinstance(queued_item[2], datetime)


@pytest.mark.asyncio
async def test_process_datagram_valid_rfc5424(server):
    """Tests processing of a valid RFC5424 datagram."""
    # Arrange
    test_data = create_test_datagram("Test message")
    addr = ("192.168.1.1", 12345)
    received_at = datetime(2025, 6, 11, 12, 0, 0)

    # Act
    params = server.process_datagram(test_data, addr, received_at)

    # Assert
    assert params is not None
    assert params["Facility"] == 4  # From priority 34
    assert params["Priority"] == 2
    assert params["FromHost"] == "testhost"
    assert params["SysLogTag"] == "testapp"
    assert params["ProcessID"] == "1234"
    assert params["Message"] == "Test message"
    assert params["ReceivedAt"] == received_at


@pytest.mark.asyncio
async def test_process_datagram_invalid_encoding(server, capsys):
    """Tests handling of a datagram with invalid UTF-8 encoding."""
    # Arrange
    test_data = b"\xff\xfe"  # Invalid UTF-8
    addr = ("192.168.1.1", 12345)
    received_at = datetime(2025, 6, 11, 12, 0, 0)

    # Act
    with patch("aiosyslogd.server.DEBUG", True):  # Enable debug output
        params = server.process_datagram(test_data, addr, received_at)

    # Assert
    assert params is None
    captured = capsys.readouterr()
    assert "Cannot decode message" in captured.out


@pytest.mark.asyncio
async def test_process_datagram_non_rfc5424(server):
    """Tests processing of a non-RFC5424 datagram with fallback parsing."""
    # Arrange
    test_data = b"<14>Invalid syslog message"
    addr = ("192.168.1.1", 12345)
    received_at = datetime(2025, 6, 11, 12, 0, 0)

    # Act
    params = server.process_datagram(test_data, addr, received_at)

    # Assert
    assert params is not None
    assert params["Facility"] == 1  # From priority 14
    assert params["Priority"] == 6
    assert params["FromHost"] == "192.168.1.1"
    assert params["SysLogTag"] == "UNKNOWN"
    assert params["Message"] == "<14>Invalid syslog message"


@pytest.mark.asyncio
async def test_debug_mode_invalid_datagram(server, capsys):
    """Tests that a debug message is printed when an invalid datagram is received in DEBUG mode."""
    with patch("aiosyslogd.server.DEBUG", True):
        test_data = b"this is not a syslog message"  # Non-RFC5424 message
        addr = ("192.168.1.1", 12345)
        received_at = datetime(2025, 6, 11, 12, 0, 0)

        # Process the datagram
        params = server.process_datagram(test_data, addr, received_at)

        # Capture console output and verify debug message
        captured = capsys.readouterr()
        assert (
            "Failed to parse as RFC-5424: this is not a syslog message"
            in captured.out
        )
        # Ensure params are still returned for fallback processing
        assert params is not None
        assert params["Message"] == "this is not a syslog message"


@pytest.mark.asyncio
async def test_debug_mode_decoding_error(server, capsys):
    """Tests that a debug message is printed when a decoding error occurs in DEBUG mode."""
    with patch("aiosyslogd.server.DEBUG", True):
        test_data = b"\xff\xfe"  # Invalid UTF-8 encoding
        addr = ("192.168.1.1", 12345)
        received_at = datetime(2025, 6, 11, 12, 0, 0)

        # Process the datagram
        params = server.process_datagram(test_data, addr, received_at)

        # Capture console output and verify debug message
        captured = capsys.readouterr()
        assert (
            "Cannot decode message from ('192.168.1.1', 12345): b'\\xff\\xfe'"
            in captured.out
        )
        # Ensure no params are returned due to decoding failure
        assert params is None
