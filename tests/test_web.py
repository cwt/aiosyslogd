from unittest.mock import patch, AsyncMock, MagicMock
import aiosqlite
import pytest
import sqlite3
import sys

# --- Import the module and app to be tested ---
from aiosyslogd import web
from aiosyslogd.db import sqlite_utils


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


@patch("aiosyslogd.db.sqlite_utils.glob")
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
        available_dbs = sqlite_utils.get_available_databases(mock_config)

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
    start_id, end_id, debug_queries = await sqlite_utils.get_time_boundary_ids(
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
    start_id_none, end_id_none, _ = await sqlite_utils.get_time_boundary_ids(
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
    filters = {
        "from_host": "server-01",
        "received_at_min": "",
        "received_at_max": "",
    }
    last_id = 1000
    page_size = 50
    direction = "next"
    start_id = 500
    end_id = 1500

    # --- Act: Call the function with all parameters ---
    result = sqlite_utils.build_log_query(
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


# --- NEW TEST CLASS ---
class TestLogQuery:
    """Tests for the LogQuery class."""

    def test_log_query_initialization(self):
        """
        Tests that the LogQuery class initializes correctly and sets the
        use_approximate_count flag based on the context.
        """
        # --- Arrange: Case 1 - Approximate count should be TRUE ---
        # No search query, no host filter, but has a time filter
        ctx_approx = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="",
            filters={"from_host": "", "received_at_min": "2025-01-01T00:00"},
            last_id=None,
            direction="next",
            page_size=50,
        )

        # --- Arrange: Case 2 - Approximate count should be FALSE ---
        # Has a search query
        ctx_no_approx_search = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="error",
            filters={"from_host": "", "received_at_min": "2025-01-01T00:00"},
            last_id=None,
            direction="next",
            page_size=50,
        )

        # --- Arrange: Case 3 - Approximate count should be FALSE ---
        # Has a host filter
        ctx_no_approx_host = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="",
            filters={
                "from_host": "server-1",
                "received_at_min": "2025-01-01T00:00",
            },
            last_id=None,
            direction="next",
            page_size=50,
        )

        # --- Arrange: Case 4 - Approximate count should be FALSE ---
        # No time filter
        ctx_no_approx_no_time = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="",
            filters={"from_host": ""},
            last_id=None,
            direction="next",
            page_size=50,
        )

        # --- Act ---
        query_approx = sqlite_utils.LogQuery(ctx_approx)
        query_no_approx_search = sqlite_utils.LogQuery(ctx_no_approx_search)
        query_no_approx_host = sqlite_utils.LogQuery(ctx_no_approx_host)
        query_no_approx_no_time = sqlite_utils.LogQuery(ctx_no_approx_no_time)

        # --- Assert ---
        assert query_approx.use_approximate_count is True
        assert query_no_approx_search.use_approximate_count is False
        assert query_no_approx_host.use_approximate_count is False
        assert query_no_approx_no_time.use_approximate_count is False
        assert query_approx.ctx == ctx_approx
        assert query_approx.results["logs"] == []

    @pytest.mark.asyncio
    async def test_log_query_run_standard_path(self):
        """
        Tests the LogQuery.run() method for a standard query (e.g., with FTS).
        It should use the exact COUNT(*) and standard query building.
        """
        # --- Arrange ---
        ctx = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="critical error",
            filters={
                "from_host": "",
                "received_at_min": "",
                "received_at_max": "",
            },
            last_id=None,
            direction="next",
            page_size=50,
        )

        mock_logs = [{"ID": 1, "Message": "critical error log"}]

        # 1. Mock the final cursor object
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = [10]
        mock_cursor.fetchall.return_value = mock_logs

        # 2. Mock the async context manager that `conn.execute` returns
        mock_execute_cm = AsyncMock()
        mock_execute_cm.__aenter__.return_value = mock_cursor

        # 3. Mock the connection object
        mock_conn = AsyncMock()
        # THIS IS THE KEY: .execute must be a SYNC function that RETURNS the async context manager
        mock_conn.execute = MagicMock(return_value=mock_execute_cm)

        # 4. Mock the async context manager that `aiosqlite.connect` returns
        mock_connect_cm = AsyncMock()
        mock_connect_cm.__aenter__.return_value = mock_conn

        # --- Act ---
        # Patch aiosqlite.connect to be a function that returns our connect context manager
        with patch(
            "aiosqlite.connect", return_value=mock_connect_cm
        ) as mock_connect_func:
            with patch(
                "aiosyslogd.db.sqlite_utils.get_time_boundary_ids"
            ) as mock_get_bounds:
                log_query = sqlite_utils.LogQuery(ctx)
                results = await log_query.run()

        # --- Assert ---
        assert not log_query.use_approximate_count
        assert results["error"] is None
        assert results["total_logs"] == 10
        assert results["logs"] == mock_logs
        assert "Applied fast-path adjustment" not in " ".join(
            results["debug_info"]
        )

        # Since time filters are empty, get_time_boundary_ids should not be called
        mock_get_bounds.assert_not_called()

        # Verify connect was called correctly
        mock_connect_func.assert_called_once_with(
            "file:test.db?mode=ro",
            uri=True,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )

        # Verify that execute was called twice (once for count, once for logs)
        assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error_to_raise", [aiosqlite.OperationalError, aiosqlite.DatabaseError]
    )
    async def test_log_query_run_handles_db_errors(self, error_to_raise):
        """
        Tests that LogQuery.run() gracefully handles database exceptions.
        """
        # --- Arrange ---
        ctx = sqlite_utils.QueryContext(
            db_path="locked.db",
            search_query="",
            filters={},
            last_id=None,
            direction="next",
            page_size=50,
        )
        error_message = "database is locked"

        # Patch aiosqlite.connect to raise the specified error
        with patch(
            "aiosqlite.connect", side_effect=error_to_raise(error_message)
        ) as mock_connect:
            # --- Act ---
            log_query = sqlite_utils.LogQuery(ctx)
            results = await log_query.run()

        # --- Assert ---
        mock_connect.assert_called_once()
        assert results["error"] == error_message
        assert results["logs"] == []
        assert results["total_logs"] == 0

    @pytest.mark.asyncio
    async def test_determine_query_boundaries(self):
        """
        Tests that _determine_query_boundaries correctly calls the helper
        function and sets the instance attributes.
        """
        # --- Arrange ---
        ctx = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="",
            filters={
                "received_at_min": "2025-01-01T00:00",
                "received_at_max": "2025-01-01T01:00",
            },
            last_id=None,
            direction="next",
            page_size=50,
        )
        log_query = sqlite_utils.LogQuery(ctx)
        log_query.conn = AsyncMock()  # Mock the connection attribute

        mock_return_value = (100, 200, ["Debug info for boundaries"])

        with patch(
            "aiosyslogd.db.sqlite_utils.get_time_boundary_ids",
            return_value=mock_return_value,
        ) as mock_get_bounds:
            # --- Act ---
            await log_query._determine_query_boundaries()

        # --- Assert ---
        mock_get_bounds.assert_called_once_with(
            log_query.conn, "2025-01-01T00:00", "2025-01-01T01:00"
        )
        assert log_query.start_id == 100
        assert log_query.end_id == 200
        assert log_query.results["debug_info"] == ["Debug info for boundaries"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "use_approx, start_id, end_id, expected_count, should_call_db",
        [
            # Case 1: Approximate count is used.
            (True, 101, 200, 100, False),
            # Case 2: Standard COUNT(*) is used because use_approx is False.
            (False, None, None, 500, True),
            # Case 3: Standard COUNT(*) is used because end_id is missing.
            (True, 101, None, 500, True),
        ],
    )
    async def test_get_total_log_count(
        self, use_approx, start_id, end_id, expected_count, should_call_db
    ):
        """
        Tests that _get_total_log_count correctly uses the approximate path
        or the standard COUNT(*) query based on the conditions.
        """
        # --- Arrange ---
        ctx = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="",
            filters={},
            last_id=None,
            direction="next",
            page_size=50,
        )
        log_query = sqlite_utils.LogQuery(ctx)
        log_query.use_approximate_count = use_approx
        log_query.start_id = start_id
        log_query.end_id = end_id

        # Mock the connection and its execute method
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = [
            500
        ]  # Mock return for standard path
        mock_execute_cm = AsyncMock()
        mock_execute_cm.__aenter__.return_value = mock_cursor
        mock_conn = AsyncMock()
        mock_conn.execute = MagicMock(return_value=mock_execute_cm)
        log_query.conn = mock_conn

        with patch(
            "aiosyslogd.db.sqlite_utils.build_log_query"
        ) as mock_build_query:
            # --- Act ---
            await log_query._get_total_log_count()

        # --- Assert ---
        assert log_query.results["total_logs"] == expected_count

        if should_call_db:
            # Verify that the standard query path was taken
            mock_build_query.assert_called_once()
            mock_conn.execute.assert_called_once()
        else:
            # Verify that the approximate path was taken (no DB calls)
            mock_build_query.assert_not_called()
            mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "use_approx, last_id, start_id, end_id, expected_start_id_in_query",
        [
            # Case 1: Fast path. first page, simple query. start_id should be adjusted.
            (True, None, 1, 1000, 900),  # 1000 - 50 - 50
            # Case 2: Standard path. Not a simple query. start_id should NOT be adjusted.
            (False, None, 1, 1000, 1),
            # Case 3: Standard path. Not the first page (last_id is set). start_id should NOT be adjusted.
            (True, 950, 1, 1000, 1),
            # Case 4: Fast path, but no end_id. start_id should NOT be adjusted.
            (True, None, 1, None, 1),
        ],
    )
    async def test_fetch_log_page(
        self, use_approx, last_id, start_id, end_id, expected_start_id_in_query
    ):
        """
        Tests that _fetch_log_page correctly adjusts the start_id for the fast-path
        and uses the original start_id for all other cases.
        """
        # --- Arrange ---
        ctx = sqlite_utils.QueryContext(
            db_path="test.db",
            search_query="",
            filters={},
            last_id=last_id,
            direction="next",
            page_size=50,
        )
        log_query = sqlite_utils.LogQuery(ctx)
        log_query.use_approximate_count = use_approx
        log_query.start_id = start_id
        log_query.end_id = end_id

        # Mock the connection and its execute method
        mock_cursor = AsyncMock()
        mock_cursor.fetchall.return_value = [{"ID": 1}]
        mock_execute_cm = AsyncMock()
        mock_execute_cm.__aenter__.return_value = mock_cursor
        mock_conn = AsyncMock()
        mock_conn.execute = MagicMock(return_value=mock_execute_cm)
        log_query.conn = mock_conn

        with patch(
            "aiosyslogd.db.sqlite_utils.build_log_query"
        ) as mock_build_query:
            # --- Act ---
            await log_query._fetch_log_page()

        # --- Assert ---
        assert log_query.results["logs"] == [{"ID": 1}]
        mock_build_query.assert_called_once()

        # Check the arguments passed to build_log_query
        call_args = mock_build_query.call_args[0]
        final_start_id = call_args[5]  # effective_start_id is the 6th argument

        assert final_start_id == expected_start_id_in_query

    # fmt: off
    @pytest.mark.parametrize(
        "direction, last_id, input_logs, expected_logs_len, expected_page_info",
        [
            # Case 1: Next page, from start, more results available. Fetched 51 descending logs (e.g., ID 100 down to 50).
            ("next", None, [{"ID": i} for i in range(100, 49, -1)], 50, {"has_next_page": True, "has_prev_page": False, "next_last_id": 51, "prev_last_id": 100}),
            # Case 2: Next page, from start, no more results. Fetched 30 descending logs.
            ("next", None, [{"ID": i} for i in range(30, 0, -1)], 30, {"has_next_page": False, "has_prev_page": False, "next_last_id": 1, "prev_last_id": 30}),
            # Case 3: Next page, from middle, more results. last_id=100. Fetched 51 logs (99 down to 49).
            ("next", 100, [{"ID": i} for i in range(99, 48, -1)], 50, {"has_next_page": True, "has_prev_page": True, "next_last_id": 50, "prev_last_id": 99}),
            # Case 4: Next page, from middle, no more results. last_id=31. Fetched 30 logs (30 down to 1).
            ("next", 31, [{"ID": i} for i in range(30, 0, -1)], 30, {"has_next_page": False, "has_prev_page": True, "next_last_id": 1, "prev_last_id": 30}),
            # Case 5: Previous page, from middle, more results. last_id=100. Fetched 51 ascending logs (101 to 151).
            ("prev", 100, [{"ID": i} for i in range(101, 152)], 50, {"has_next_page": True, "has_prev_page": True, "next_last_id": 102, "prev_last_id": 151}),
            # Case 6: Previous page, to the first page. last_id=101. Fetched 30 ascending logs (102 to 131).
            ("prev", 101, [{"ID": i} for i in range(102, 132)], 30, {"has_next_page": True, "has_prev_page": False, "next_last_id": 102, "prev_last_id": 131}),
            # Case 7: No logs at all.
            ("next", None, [], 0, {"has_next_page": False, "has_prev_page": False, "next_last_id": None, "prev_last_id": None}),
        ]
    )
    def test_prepare_pagination(
        self, direction, last_id, input_logs, expected_logs_len, expected_page_info
    ):
        """
        Tests that _prepare_pagination correctly sets flags and trims the log list.
        """
        # --- Arrange ---
        ctx = sqlite_utils.QueryContext(
            db_path="",
            search_query="",
            filters={},
            last_id=last_id,
            direction=direction,
            page_size=50,
        )
        log_query = sqlite_utils.LogQuery(ctx)
        # Use a copy of the list to prevent mutation issues.
        log_query.results["logs"] = input_logs.copy()

        # --- Act ---
        log_query._prepare_pagination()

        # --- Assert ---
        assert len(log_query.results["logs"]) == expected_logs_len
        assert log_query.results["page_info"]["has_next_page"] == expected_page_info["has_next_page"]
        assert log_query.results["page_info"]["has_prev_page"] == expected_page_info["has_prev_page"]
        assert log_query.results["page_info"]["next_last_id"] == expected_page_info["next_last_id"]
        assert log_query.results["page_info"]["prev_last_id"] == expected_page_info["prev_last_id"]

        # This assertion is now valid because the original input_logs list is not mutated.
        if direction == "prev" and input_logs:
            assert log_query.results["logs"][0]["ID"] == input_logs[-1]["ID"]
    # fmt: on
