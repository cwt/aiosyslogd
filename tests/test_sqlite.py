import pytest
import asyncio
from datetime import datetime, timedelta
import os
import aiosqlite
import sqlite3
from typing import List, Dict, Any


# --- New Datetime Adapters to fix DeprecationWarning ---
def adapt_datetime_iso(val):
    """Adapt datetime.datetime to timezone-aware ISO 8601 string."""
    return val.isoformat()


def convert_timestamp(val):
    """Convert ISO 8601 string from DB back to datetime.datetime object."""
    return datetime.fromisoformat(val.decode())


# Register the new adapters with the sqlite3 module
sqlite3.register_adapter(datetime, adapt_datetime_iso)
sqlite3.register_converter("timestamp", convert_timestamp)

# --- Import the real SQLiteDriver from the application source code ---
# This assumes your project is structured so pytest can find the aiosyslogd package.
from aiosyslogd.db.sqlite import SQLiteDriver


# --- Helper Function for Test Data ---
def create_log_entry(message: str, timestamp: datetime) -> Dict[str, Any]:
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
def tmp_db_path(tmp_path):
    """Provides a temporary path for the database file."""
    return tmp_path / "test_syslog.sqlite3"


@pytest.fixture
def driver(tmp_db_path, event_loop):
    """Provides an instance of SQLiteDriver and ensures cleanup."""
    config = {"database": str(tmp_db_path), "debug": False, "sql_dump": False}
    d = SQLiteDriver(config)
    yield d
    event_loop.run_until_complete(d.close())


# --- Test Cases ---


@pytest.mark.asyncio
async def test_write_batch_single_month_fast_path(driver, tmp_db_path):
    """
    Tests the 'fast path' where an entire batch belongs to a single month.
    """
    # 1. ARRANGE
    log_time = datetime(2025, 7, 15, 10, 0, 0)
    log_batch = [
        create_log_entry("normal log 1", log_time),
        create_log_entry("normal log 2", log_time + timedelta(seconds=1)),
    ]
    db_filename = "test_syslog_202507.sqlite3"

    # 2. ACT
    await driver.write_batch(log_batch)

    # 3. ASSERT
    db_path = tmp_db_path.parent / db_filename
    assert os.path.exists(db_path), "Database file was not created for July"

    conn = await aiosqlite.connect(
        db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
    )
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM SystemEvents")
        count = await cursor.fetchone()
    await conn.close()

    assert count[0] == 2, "Should have written exactly 2 logs"


@pytest.mark.asyncio
async def test_write_batch_across_month_boundary(driver, tmp_db_path):
    """
    Tests that a batch of logs spanning a month boundary is correctly
    partitioned and written to two separate database files.
    """
    # 1. ARRANGE
    end_of_may = datetime(2025, 5, 31, 23, 59, 58)
    log_batch = [
        create_log_entry("log_may_1", end_of_may),
        create_log_entry("log_may_2", end_of_may + timedelta(seconds=1)),
        create_log_entry("log_june_1", end_of_may + timedelta(seconds=2)),
        create_log_entry("log_june_2", end_of_may + timedelta(seconds=3)),
        create_log_entry("log_june_3", end_of_may + timedelta(seconds=4)),
    ]

    # 2. ACT
    await driver.write_batch(log_batch)

    # 3. ASSERT
    db_dir = tmp_db_path.parent
    may_db_path = db_dir / "test_syslog_202505.sqlite3"
    june_db_path = db_dir / "test_syslog_202506.sqlite3"

    assert os.path.exists(may_db_path), "May database file was not created"
    assert os.path.exists(june_db_path), "June database file was not created"

    may_conn = await aiosqlite.connect(
        may_db_path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    async with may_conn.cursor() as cursor:
        await cursor.execute(
            "SELECT Message FROM SystemEvents ORDER BY ReceivedAt"
        )
        may_logs = [row[0] for row in await cursor.fetchall()]
    await may_conn.close()

    assert len(may_logs) == 2, "May database should have exactly 2 logs"
    assert may_logs == [
        "log_may_1",
        "log_may_2",
    ], "Incorrect logs found in May database"

    june_conn = await aiosqlite.connect(
        june_db_path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    async with june_conn.cursor() as cursor:
        await cursor.execute(
            "SELECT Message FROM SystemEvents ORDER BY ReceivedAt"
        )
        june_logs = [row[0] for row in await cursor.fetchall()]
    await june_conn.close()

    assert len(june_logs) == 3, "June database should have exactly 3 logs"
    assert june_logs == [
        "log_june_1",
        "log_june_2",
        "log_june_3",
    ], "Incorrect logs found in June database"


@pytest.mark.asyncio
async def test_fts_search_functionality(driver, tmp_db_path):
    """
    Tests that the FTS5 virtual table is created and can be searched.
    Includes a manual rebuild to ensure robustness on buggy SQLite versions.
    """
    # 1. ARRANGE
    log_time = datetime(2025, 8, 1, 12, 0, 0)
    log_batch = [
        create_log_entry("This is a success message", log_time),
        create_log_entry("This is a critical failure message", log_time),
        create_log_entry("Another success log", log_time),
    ]
    db_filename = "test_syslog_202508.sqlite3"
    db_path = tmp_db_path.parent / db_filename

    # 2. ACT
    await driver.write_batch(log_batch)

    # Force a rebuild for test robustness on buggy sqlite versions
    rebuild_conn = await aiosqlite.connect(db_path)
    await rebuild_conn.execute(
        "INSERT INTO SystemEvents_FTS(SystemEvents_FTS) VALUES('rebuild')"
    )
    await rebuild_conn.commit()
    await rebuild_conn.close()

    # 3. ASSERT
    conn = await aiosqlite.connect(
        db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
    )
    async with conn.cursor() as cursor:
        # Search for a specific word
        await cursor.execute(
            "SELECT Message FROM SystemEvents_FTS WHERE Message MATCH 'failure'"
        )
        failure_logs = await cursor.fetchall()

        # Search using a prefix
        await cursor.execute(
            "SELECT Message FROM SystemEvents_FTS WHERE Message MATCH 'succ*'"
        )
        success_logs = await cursor.fetchall()

    await conn.close()

    assert len(failure_logs) == 1, "Should find exactly one log with 'failure'"
    assert failure_logs[0][0] == "This is a critical failure message"

    assert (
        len(success_logs) == 2
    ), "Should find two logs with words starting with 'succ'"


@pytest.mark.asyncio
async def test_write_empty_batch(driver, tmp_db_path):
    """
    Tests that writing an empty batch does not cause errors or create files.
    """
    # 1. ARRANGE
    log_batch = []

    # 2. ACT
    await driver.write_batch(log_batch)

    # 3. ASSERT
    # Check that no database files were created in the temporary directory
    db_dir = tmp_db_path.parent
    files = os.listdir(db_dir)
    assert (
        len(files) == 0
    ), "No database files should be created for an empty batch"
