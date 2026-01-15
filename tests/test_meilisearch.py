from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

# --- Import the real MeilisearchDriver from the application source code ---
# This assumes your project is structured so pytest can find the aiosyslogd package.
from meilisearch_python_sdk.errors import (
    MeilisearchCommunicationError,
    MeilisearchApiError,
)
from loguru import logger

# --- Import the real MeilisearchDriver from the application source code ---
# This assumes your project is structured so pytest can find the aiosyslogd package.
from aiosyslogd.db.meilisearch import MeilisearchDriver


# --- Helper Function for Test Data ---
def create_log_entry(message: str, timestamp: datetime):
    """Creates a structured log entry for testing."""
    return {
        "Facility": 1,
        "Priority": 5,
        "FromHost": "testhost",
        "InfoUnitID": 1,
        "DeviceReportedTime": timestamp,
        "SysLogTag": "test-app",
        "ProcessID": "123",
        "Message": message,
        "ReceivedAt": timestamp,
    }


# --- Pytest Fixtures ---
@pytest.fixture
def mock_client():
    """
    A fixture to provide a correctly configured mocked AsyncClient.
    The key is that client.index() is a regular method that returns a mock Index object,
    and the Index object's methods are async.
    """
    # This is the mock for the Index object. Its methods are async.
    mock_index_obj = AsyncMock()
    mock_index_obj.add_documents.return_value = AsyncMock(task_uid=123)
    mock_index_obj.update_settings.return_value = AsyncMock(task_uid=456)

    # This is the main client mock.
    client_mock = AsyncMock()
    # Configure client.index() to be a regular method that returns our mock_index_obj
    client_mock.index = MagicMock(return_value=mock_index_obj)

    return client_mock


@pytest.fixture
def driver(mock_client):
    """Provides an instance of MeilisearchDriver with a mocked client."""
    # Patch the AsyncClient during instantiation
    with patch(
        "aiosyslogd.db.meilisearch.AsyncClient", return_value=mock_client
    ):
        config = {
            "url": "http://mock-meili:7700",
            "api_key": "mock_key",
            "debug": True,
        }
        d = MeilisearchDriver(config)
        yield d


# --- Test Cases ---


@pytest.mark.asyncio
async def test_write_batch_single_index(driver, mock_client):
    """
    Tests that a batch of logs for a single month correctly calls Meilisearch client methods.
    """
    # 1. ARRANGE
    log_time = datetime(2025, 9, 10, 14, 0, 0)
    log_batch = [
        create_log_entry("Log entry 1", log_time),
        create_log_entry("Log entry 2", log_time),
    ]
    expected_index_name = "SystemEvents202509"

    # 2. ACT
    await driver.write_batch(log_batch)

    # 3. ASSERT
    # Verify index creation and configuration was attempted
    mock_client.create_index.assert_called_once_with(
        uid=expected_index_name, primary_key="id"
    )
    mock_client.index.assert_called_with(expected_index_name)

    # Get the mock Index object returned by client.index()
    mock_index_object = mock_client.index.return_value
    mock_index_object.update_settings.assert_called_once()
    mock_client.wait_for_task.assert_any_call(456)  # Settings task

    # Verify documents were added
    mock_index_object.add_documents.assert_called_once()
    # Check that the documents passed to the mock have the correct structure and count
    added_docs = mock_index_object.add_documents.call_args[0][0]
    assert len(added_docs) == 2
    assert added_docs[0]["Message"] == "Log entry 1"
    assert "id" in added_docs[0]  # Ensure an ID was added

    # Verify the driver waited for the add_documents task to complete
    mock_client.wait_for_task.assert_any_call(123)  # Document add task


@pytest.mark.asyncio
async def test_write_batch_across_month_boundary(driver, mock_client):
    """
    Tests that a batch spanning a month boundary correctly creates two indexes and partitions the data.
    """
    # 1. ARRANGE
    end_of_sept = datetime(2025, 9, 30, 23, 59, 59)
    log_batch = [
        create_log_entry("Log from September", end_of_sept),
        create_log_entry(
            "Log from October", end_of_sept + timedelta(seconds=2)
        ),  # Crosses into Oct
    ]

    # 2. ACT
    await driver.write_batch(log_batch)

    # 3. ASSERT
    # Verify index creation was attempted for both months
    mock_client.create_index.assert_any_call(
        uid="SystemEvents202509", primary_key="id"
    )
    mock_client.create_index.assert_any_call(
        uid="SystemEvents202510", primary_key="id"
    )
    assert mock_client.create_index.call_count == 2

    # Verify documents were added in two separate calls
    mock_index_object = mock_client.index.return_value
    assert mock_index_object.add_documents.call_count == 2

    # Check the contents of each call to add_documents
    call_args_list = mock_index_object.add_documents.call_args_list
    sept_call_docs = call_args_list[0].args[0]
    oct_call_docs = call_args_list[1].args[0]

    assert len(sept_call_docs) == 1
    assert sept_call_docs[0]["Message"] == "Log from September"

    assert len(oct_call_docs) == 1
    assert oct_call_docs[0]["Message"] == "Log from October"


@pytest.mark.asyncio
async def test_ensure_index_is_created_only_once(driver, mock_client):
    """
    Tests that the driver caches index creation status and doesn't try to recreate an index.
    """
    # 1. ARRANGE
    log_time = datetime(2025, 11, 5, 10, 0, 0)
    batch1 = [create_log_entry("First batch log", log_time)]
    batch2 = [create_log_entry("Second batch log", log_time)]

    # 2. ACT
    await driver.write_batch(batch1)
    await driver.write_batch(batch2)

    # 3. ASSERT
    # Verify that create_index was only called once for the same index name
    mock_client.create_index.assert_called_once_with(
        uid="SystemEvents202511", primary_key="id"
    )

    # Verify that documents were still added twice
    mock_index_object = mock_client.index.return_value
    assert mock_index_object.add_documents.call_count == 2


@pytest.mark.asyncio
async def test_connect_communication_error(mock_client):
    """Tests that a MeilisearchCommunicationError is raised on connection failure."""
    # 1. ARRANGE
    mock_client.health.side_effect = MeilisearchCommunicationError(
        "Network Error"
    )
    config = {
        "url": "http://mock-meili:7700",
        "api_key": "mock_key",
        "debug": True,
    }
    with patch(
        "aiosyslogd.db.meilisearch.AsyncClient", return_value=mock_client
    ):
        driver = MeilisearchDriver(config)

    # 2. ACT & ASSERT
    with pytest.raises(MeilisearchCommunicationError):
        await driver.connect()


@pytest.mark.asyncio
async def test_connect_unavailable(mock_client):
    """Tests that a ConnectionError is raised when Meilisearch is not available."""
    # 1. ARRANGE
    mock_client.health.return_value = MagicMock(status="unavailable")
    config = {
        "url": "http://mock-meili:7700",
        "api_key": "mock_key",
        "debug": True,
    }
    with patch(
        "aiosyslogd.db.meilisearch.AsyncClient", return_value=mock_client
    ):
        driver = MeilisearchDriver(config)

    # 2. ACT & ASSERT
    with pytest.raises(ConnectionError):
        await driver.connect()


@pytest.mark.asyncio
async def test_write_batch_add_documents_fails(driver, mock_client):
    """Tests that an exception during add_documents is handled."""
    # 1. ARRANGE
    log_sink = []

    def sink_function(message):
        log_sink.append(message)

    handler_id = logger.add(sink_function)

    log_time = datetime(2025, 9, 10, 14, 0, 0)
    log_batch = [create_log_entry("Log entry 1", log_time)]
    mock_index_object = mock_client.index.return_value
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_index_object.add_documents.side_effect = MeilisearchApiError(
        "API Error", mock_response
    )

    # 2. ACT
    await driver.write_batch(log_batch)

    # 3. ASSERT
    assert any("Error writing to Meilisearch" in record for record in log_sink)
    logger.remove(handler_id)


@pytest.mark.asyncio
async def test_write_batch_wait_for_task_fails(driver, mock_client):
    """Tests that an exception during wait_for_task is handled."""
    # 1. ARRANGE
    log_sink = []

    def sink_function(message):
        log_sink.append(message)

    handler_id = logger.add(sink_function)

    log_time = datetime(2025, 9, 10, 14, 0, 0)
    log_batch = [create_log_entry("Log entry 1", log_time)]
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_client.wait_for_task.side_effect = MeilisearchApiError(
        "Task Error", mock_response
    )

    # 2. ACT
    await driver.write_batch(log_batch)

    # 3. ASSERT
    assert any("Error writing to Meilisearch" in record for record in log_sink)
    logger.remove(handler_id)
