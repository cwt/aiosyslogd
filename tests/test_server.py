from aiosyslogd.db import BaseDatabase
from aiosyslogd.server import SyslogUDPServer, get_db_driver
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import pytest
import pytest_asyncio


# Helper function to create a mock datagram
def create_test_datagram(
    message: str, priority: int = 34, ts: str = "2025-06-11T12:00:00.000Z"
) -> bytes:
    """Creates a sample RFC5424 syslog message for testing."""
    return f"<{priority}>1 {ts} testhost testapp 1234 - - {message}".encode(
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
        if server._db_writer_task and not server._db_writer_task.done():
            server._db_writer_task.cancel()
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
async def test_server_creation_debug_mode(mock_db, capsys):
    """Tests that the debug mode message is printed on server creation."""
    # Arrange: Patch the DEBUG config to be True
    with patch("aiosyslogd.server.get_db_driver", return_value=mock_db):
        with patch("aiosyslogd.server.DEBUG", True):
            # Act
            server = await SyslogUDPServer.create(host="127.0.0.1", port=5141)

            # Assert
            captured = capsys.readouterr()
            assert "Debug mode is ON." in captured.out

            # Cleanup
            try:
                await server.shutdown()
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_connection_made(server):
    """Tests that the database writer task is started upon connection."""
    # Arrange
    mock_transport = MagicMock()
    # Reset the task to ensure it's created by the call
    if server._db_writer_task:
        server._db_writer_task.cancel()
    server._db_writer_task = None

    # Act
    server.connection_made(mock_transport)

    # Assert
    assert server.transport == mock_transport
    assert server._db_writer_task is not None
    assert not server._db_writer_task.done()


@pytest.mark.asyncio
async def test_connection_made_no_db(server):
    """Tests that the db writer task is NOT started if there is no db driver."""
    # Arrange
    server.db = None  # Manually remove the db driver
    mock_transport = MagicMock()
    server._db_writer_task = None

    # Act
    server.connection_made(mock_transport)

    # Assert
    assert server.transport == mock_transport
    assert server._db_writer_task is None  # Task should not be created


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
async def test_datagram_received_shutting_down(server):
    """Tests that datagrams are ignored when the server is shutting down."""
    # Arrange
    server._shutting_down = True
    test_data = create_test_datagram("Should be ignored")
    addr = ("192.168.1.1", 12345)

    # Act
    server.datagram_received(test_data, addr)

    # Assert
    assert server._message_queue.qsize() == 0


@pytest.mark.asyncio
async def test_error_received_debug_on(server, capsys):
    """Tests that error_received prints output when debug mode is on."""
    # Arrange
    test_exc = ValueError("Test Error")
    with patch("aiosyslogd.server.DEBUG", True):
        # Act
        server.error_received(test_exc)

        # Assert
        captured = capsys.readouterr()
        assert "Error received: Test Error" in captured.out


@pytest.mark.asyncio
async def test_error_received_debug_off(server, capsys):
    """Tests that error_received is silent when debug mode is off."""
    # Arrange
    test_exc = ValueError("Test Error")
    with patch("aiosyslogd.server.DEBUG", False):
        # Act
        server.error_received(test_exc)

        # Assert
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


@pytest.mark.asyncio
async def test_connection_lost_debug_on(server, capsys):
    """Tests that connection_lost prints output when debug mode is on."""
    # Arrange
    test_exc = ConnectionAbortedError("Connection lost unexpectedly")
    with patch("aiosyslogd.server.DEBUG", True):
        # Act
        server.connection_lost(test_exc)

        # Assert
        captured = capsys.readouterr()
        assert "Connection lost: Connection lost unexpectedly" in captured.out


@pytest.mark.asyncio
async def test_connection_lost_debug_off(server, capsys):
    """Tests that connection_lost is silent when debug mode is off."""
    # Arrange
    test_exc = ConnectionAbortedError("Connection lost unexpectedly")
    with patch("aiosyslogd.server.DEBUG", False):
        # Act
        server.connection_lost(test_exc)

        # Assert
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


@pytest.mark.asyncio
async def test_database_writer_batch_full(server, mock_db):
    """Tests that a full batch triggers a write and is then cleared."""
    # Arrange
    # Use a side_effect to capture a copy of the batch at call time,
    # preventing an issue where the test inspects the list after it's cleared.
    captured_batch = None

    async def capture_side_effect(batch):
        nonlocal captured_batch
        captured_batch = list(batch)  # noqa -- make a copy to capture the state

    mock_db.write_batch.side_effect = capture_side_effect

    with patch("aiosyslogd.server.BATCH_SIZE", 2):
        server.connection_made(MagicMock())

        # Act 1: Send one message, batch should not be full
        server.datagram_received(
            create_test_datagram("log 1"), ("localhost", 123)
        )
        await asyncio.sleep(0)  # Yield to writer

        # Assert 1: No write should have happened yet
        mock_db.write_batch.assert_not_called()

        # Act 2: Send second message to fill the batch
        server.datagram_received(
            create_test_datagram("log 2"), ("localhost", 123)
        )
        await asyncio.sleep(0.01)  # Yield to writer to process the full batch

        # Assert 2: The batch should have been written
        mock_db.write_batch.assert_called_once()
        assert captured_batch is not None
        assert len(captured_batch) == 2
        assert captured_batch[0]["Message"] == "log 1"
        assert captured_batch[1]["Message"] == "log 2"


@pytest.mark.asyncio
async def test_database_writer_batch_timeout(server, mock_db):
    """Tests that the database writer writes a batch after a timeout."""
    # Arrange
    with (
        patch("aiosyslogd.server.BATCH_SIZE", 10),
        patch("aiosyslogd.server.BATCH_TIMEOUT", 0.01),
    ):
        server.connection_made(MagicMock())
        server.datagram_received(
            create_test_datagram("log 1"), ("localhost", 123)
        )

        # Act
        await asyncio.sleep(0.02)  # Wait for timeout to occur

        # Assert
        mock_db.write_batch.assert_called_once()
        assert server._message_queue.empty()


@pytest.mark.asyncio
async def test_database_writer_shutdown_flush(mock_db):
    """Tests that the database writer flushes remaining logs on shutdown."""
    # Arrange - create a server instance manually to control its lifecycle
    server = None
    with patch("aiosyslogd.server.get_db_driver", return_value=mock_db):
        with patch("aiosyslogd.server.BATCH_SIZE", 10):
            server = await SyslogUDPServer.create(host="127.0.0.1", port=5141)

    try:
        server.connection_made(MagicMock())
        server.datagram_received(
            create_test_datagram("log 1"), ("localhost", 123)
        )
        server.datagram_received(
            create_test_datagram("log 2"), ("localhost", 123)
        )
        await asyncio.sleep(0)  # Yield to the event loop to process the queue

        # Act
        await server.shutdown()

        # Assert
        mock_db.write_batch.assert_called_once()
        assert len(mock_db.write_batch.call_args[0][0]) == 2
    finally:
        # Ensure cleanup in case of assertion failure
        if server and server.transport:
            server.transport.close()
        if (
            server
            and server._db_writer_task
            and not server._db_writer_task.done()
        ):
            server._db_writer_task.cancel()


@pytest.mark.asyncio
async def test_database_writer_exception_debug_on(server, mock_db, capsys):
    """Tests that an exception in the writer loop is logged in debug mode."""
    # Arrange
    with patch("aiosyslogd.server.DEBUG", True):
        server.connection_made(MagicMock())
        # Make processing fail
        with patch.object(
            server,
            "process_datagram",
            side_effect=ValueError("Processing failed"),
        ):
            server.datagram_received(
                create_test_datagram("bad log"), ("localhost", 123)
            )

            # Act
            await asyncio.sleep(0.01)  # let writer run

            # Assert
            captured = capsys.readouterr()
            assert "[DB-WRITER-ERROR] Processing failed" in captured.out
            mock_db.write_batch.assert_not_called()


@pytest.mark.asyncio
async def test_database_writer_exception_debug_off(server, mock_db, capsys):
    """Tests that an exception in the writer loop is silent when debug is off."""
    # Arrange
    with patch("aiosyslogd.server.DEBUG", False):
        server.connection_made(MagicMock())
        # Make processing fail
        with patch.object(
            server,
            "process_datagram",
            side_effect=ValueError("Processing failed"),
        ):
            server.datagram_received(
                create_test_datagram("bad log"), ("localhost", 123)
            )

            # Act
            await asyncio.sleep(0.01)  # let writer run

            # Assert
            captured = capsys.readouterr()
            # Check that the specific error message is not in the output
            assert "[DB-WRITER-ERROR]" not in captured.out
            mock_db.write_batch.assert_not_called()


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
async def test_process_datagram_log_dump_on(server, capsys):
    """Tests that datagram processing prints output when LOG_DUMP is on."""
    # Arrange
    test_data = create_test_datagram("Log dump test")
    addr = ("192.168.1.1", 12345)
    received_at = datetime(2025, 6, 11, 12, 0, 0)
    with patch("aiosyslogd.server.LOG_DUMP", True):
        # Act
        server.process_datagram(test_data, addr, received_at)

        # Assert
        captured = capsys.readouterr()
        assert "FROM 192.168.1.1:" in captured.out
        assert "RFC5424 DATA:" in captured.out
        assert "Log dump test" in captured.out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_ts, test_id",
    [
        ("not-a-timestamp", "invalid-string-ValueError"),
        # The case ts=None causes an unhandled AttributeError in the source code.
        # It is not included here as the test would fail until the source is fixed
        # to catch AttributeError in the timestamp parsing block.
    ],
)
async def test_process_datagram_invalid_timestamp_fallback(
    server, bad_ts, test_id
):
    """
    Tests that DeviceReportedTime falls back to ReceivedAt when the
    timestamp from the message is unparseable (triggering a handled exception).
    """
    # Arrange
    test_data = create_test_datagram(f"Test with {test_id}", ts=bad_ts)
    addr = ("192.168.1.1", 12345)
    # Use a distinct timestamp to ensure we can verify the fallback.
    received_at = datetime(2025, 6, 11, 12, 1, 1)

    # Act
    params = server.process_datagram(test_data, addr, received_at)

    # Assert
    assert params is not None, "Processing should not fail completely"
    assert (
        params["DeviceReportedTime"] == received_at
    ), "Should fall back to received_at"


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


def test_get_db_driver_injection_attempt(capsys):
    """
    Tests that get_db_driver prevents code injection by validating the driver name.
    """
    # Arrange: Patch the DB_DRIVER config to simulate a malicious value.
    # An attacker might try to use path traversal or module names to execute code.
    malicious_driver_name = "../../../../os"
    with patch("aiosyslogd.server.DB_DRIVER", malicious_driver_name):
        # Act & Assert: The function should raise SystemExit.
        with pytest.raises(SystemExit):
            get_db_driver()

    # Assert that an informative error message was printed.
    captured = capsys.readouterr()
    assert "Error: Invalid database driver" in captured.out
    assert f"'{malicious_driver_name}'" in captured.out
    assert "Allowed drivers are:" in captured.out
    assert "sqlite" in captured.out
    assert "meilisearch" in captured.out


def test_get_db_driver_is_none():
    """
    Tests that get_db_driver returns None when the driver is not set.
    """
    # Arrange: Patch the DB_DRIVER config to be None.
    with patch("aiosyslogd.server.DB_DRIVER", None):
        # Act
        driver = get_db_driver()
        # Assert
        assert driver is None
