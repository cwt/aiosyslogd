from unittest.mock import patch
import aiosqlite
import pytest
import pytest_asyncio
import sys

# --- Import the module and app to be tested ---
from aiosyslogd import web


@pytest.fixture
def client():
    """Provides a test client for the Quart app."""
    app_config = web.CFG.copy()
    app_config["database"] = {"driver": "sqlite"}
    app_config["web_server"] = {
        "bind_ip": "127.0.0.1",
        "bind_port": 5141,
        "debug": False,
    }
    with patch("aiosyslogd.web.CFG", app_config):
        from aiosyslogd.web import app

        yield app.test_client()


def test_main_meilisearch_exit(capsys):
    """
    Tests that the main function exits cleanly if Meilisearch is the configured driver.
    """
    meili_config = {
        "database": {"driver": "meilisearch"},
        "web_server": {
            "bind_ip": "127.0.0.1",
            "bind_port": 5141,
            "debug": False,
        },
    }
    with patch("aiosyslogd.web.CFG", meili_config):
        with patch("aiosyslogd.web.logger.info", sys.stdout.write):
            with patch("aiosyslogd.web.logger.warning", sys.stderr.write):
                check_backend = web.check_backend()
                assert check_backend is False

                with pytest.raises(SystemExit) as e:
                    web.main()
                assert e.value.code == 0

    captured = capsys.readouterr()
    assert "Meilisearch backend is selected" in captured.out
    assert "This web UI is for the SQLite backend only" in captured.err


@pytest.mark.asyncio
async def test_index_route_no_dbs(client):
    """
    Tests the index route when no database files are found.
    """
    with patch("aiosyslogd.web.get_available_databases", return_value=[]):
        response = await client.get("/")
        assert response.status_code == 200
        response_data = await response.get_data(as_text=True)
        assert "No SQLite database files found" in response_data


@patch("aiosyslogd.web.glob")
def test_get_available_databases(mock_glob):
    """
    Tests that get_available_databases correctly finds, sorts (desc),
    and returns a list of database files based on the config pattern.
    """
    # --- Arrange ---
    # Mock the return value of glob.glob to simulate finding files
    mock_glob.glob.return_value = [
        "syslog_202505.sqlite3",
        "syslog_202507.sqlite3",
        "syslog_202506.sqlite3",
    ]
    # Mock the configuration to provide the base path for the search pattern
    mock_config = {"database": {"sqlite": {"database": "syslog.sqlite3"}}}

    # --- Act ---
    with patch("aiosyslogd.web.CFG", mock_config):
        available_dbs = web.get_available_databases()

    # --- Assert ---
    # Verify that glob.glob was called with the correct search pattern
    mock_glob.glob.assert_called_once_with("syslog_*.sqlite3")
    # Verify that the returned list is sorted in reverse chronological order
    expected_order = [
        "syslog_202507.sqlite3",
        "syslog_202506.sqlite3",
        "syslog_202505.sqlite3",
    ]
    assert available_dbs == expected_order


@pytest.mark.asyncio
async def test_get_time_boundary_ids():
    """
    Tests the get_time_boundary_ids function to ensure it correctly finds
    the first and last log IDs within a specified time range.
    """
    # --- Arrange: Create an in-memory database with test data ---
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        """CREATE TABLE SystemEvents (
           ID INTEGER PRIMARY KEY,
           ReceivedAt TIMESTAMP
        )"""
    )
    test_data = [
        (1, "2025-06-20 10:00:00"),
        (2, "2025-06-20 10:30:00"),
        (3, "2025-06-20 11:00:00"),  # This should be the start_id
        (4, "2025-06-20 11:30:00"),
        (5, "2025-06-20 12:00:00"),  # This should be the end_id
        (6, "2025-06-20 12:30:00"),
    ]
    await conn.executemany("INSERT INTO SystemEvents VALUES (?, ?)", test_data)
    await conn.commit()

    # --- Act: Call the function with a specific time window ---
    min_time = "2025-06-20T11:00:00"
    max_time = "2025-06-20T12:00:00"
    start_id, end_id, debug_queries = await web.get_time_boundary_ids(
        conn, min_time, max_time
    )

    # --- Assert: Check if the correct IDs and debug info are returned ---
    assert start_id == 3
    assert end_id == 5
    assert len(debug_queries) == 2
    assert "Boundary Query (Start)" in debug_queries[0]
    assert "Boundary Query (End)" in debug_queries[1]

    # --- Act & Assert: Test a time range with no matching logs ---
    min_time_no_match = "2025-07-01T00:00:00"
    max_time_no_match = "2025-07-01T23:59:59"
    start_id_none, end_id_none, _ = await web.get_time_boundary_ids(
        conn, min_time_no_match, max_time_no_match
    )
    assert start_id_none is None
    assert end_id_none is None

    await conn.close()


def test_build_log_query_all_filters():
    """
    Tests that build_log_query correctly assembles a query when all
    possible filters and pagination options are applied.
    """
    # --- Arrange: Define all possible inputs ---
    search_query = "error"
    filters = {"from_host": "server-01"}
    last_id = 1000
    page_size = 50
    direction = "next"
    start_id = 500
    end_id = 1500

    # --- Act: Call the function with all parameters ---
    result = web.build_log_query(
        search_query, filters, last_id, page_size, direction, start_id, end_id
    )

    # --- Assert: Check the generated SQL and parameters ---
    expected_sql = (
        "SELECT ID, FromHost, ReceivedAt, Message "
        "FROM SystemEvents "
        "WHERE ID >= ? AND ID <= ? AND FromHost = ? AND ID IN "
        "(SELECT rowid FROM SystemEvents_FTS WHERE Message MATCH ? AND rowid >= ? AND rowid <= ?) "
        "AND ID < ? "
        "ORDER BY ID DESC LIMIT 51"
    )

    expected_params = [
        500,  # start_id for main query
        1500,  # end_id for main query
        "server-01",  # from_host
        "error",  # FTS query
        500,  # start_id for FTS subquery
        1500,  # end_id for FTS subquery
        1000,  # last_id for pagination
    ]

    assert expected_sql == result["main_sql"]
    assert expected_params == result["main_params"]

    # --- Assert count query ---
    expected_count_sql = (
        "SELECT COUNT(*) "
        "FROM SystemEvents "
        "WHERE ID >= ? AND ID <= ? AND FromHost = ? AND ID IN "
        "(SELECT rowid FROM SystemEvents_FTS WHERE Message MATCH ? AND rowid >= ? AND rowid <= ?)"
    )
    expected_count_params = expected_params[
        :-1
    ]  # All except the pagination param

    assert expected_count_sql == result["count_sql"]
    assert expected_count_params == result["count_params"]


@patch("aiosyslogd.web.app.run")
@patch("aiosyslogd.web.uvloop", create=True)
@patch("aiosyslogd.web.check_backend", return_value=True)
def test_main_function_calls_app_run(
    mock_check_backend, mock_uvloop, mock_app_run
):
    """
    Tests that the main function correctly calls uvloop.install (if available)
    and then starts the Quart server with the correct host and port.
    """
    # --- Arrange ---
    # The default config provides the expected host and port.
    expected_host = web.WEB_SERVER_CFG.get("bind_ip", "127.0.0.1")
    expected_port = web.WEB_SERVER_CFG.get("bind_port", 5141)

    # --- Act ---
    web.main()

    # --- Assert ---
    mock_check_backend.assert_called_once()
    mock_uvloop.install.assert_called_once()
    mock_app_run.assert_called_once_with(
        host=expected_host, port=expected_port, debug=web.DEBUG
    )
