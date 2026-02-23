import os
import tempfile
from datetime import datetime, timedelta
import pytest

from aiosyslogd.db.sqlite import SQLiteDriver


class TestSQLiteCleanup:
    """Tests for the database cleanup functionality."""

    @pytest.fixture
    def temp_db_dir(self):
        """Creates a temporary directory for test databases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def sqlite_driver(self, temp_db_dir):
        """Creates a SQLiteDriver instance with test configuration."""
        config = {
            "database": os.path.join(temp_db_dir, "test_syslog.sqlite3"),
            "retention_months": 1,
            "sql_dump": False,
            "debug": False,
        }
        return SQLiteDriver(config)

    def test_get_database_files(self, sqlite_driver, temp_db_dir):
        """Test that _get_database_files correctly identifies database files."""
        db_path = os.path.join(temp_db_dir, "test_syslog_202501.sqlite3")
        with open(db_path, "w") as f:
            f.write("test")

        db_files = sqlite_driver._get_database_files()

        assert len(db_files) == 1
        filepath, dt = db_files[0]
        assert filepath == db_path
        assert dt.year == 2025
        assert dt.month == 1

    def test_get_database_files_multiple(self, sqlite_driver, temp_db_dir):
        """Test identification of multiple database files."""
        months = ["202501", "202502", "202503"]
        created_files = []

        for month in months:
            db_path = os.path.join(temp_db_dir, f"test_syslog_{month}.sqlite3")
            with open(db_path, "w") as f:
                f.write("test")
            created_files.append(db_path)

        db_files = sqlite_driver._get_database_files()

        assert len(db_files) == 3
        filepaths = [fp for fp, _ in db_files]
        for expected_path in created_files:
            assert expected_path in filepaths

    def test_get_database_files_invalid_name(self, sqlite_driver, temp_db_dir):
        """Test that invalid filenames are skipped with a warning."""
        invalid_path = os.path.join(temp_db_dir, "test_syslog_invalid.sqlite3")
        with open(invalid_path, "w") as f:
            f.write("test")

        db_files = sqlite_driver._get_database_files()

        assert len(db_files) == 0

    @pytest.mark.asyncio
    async def test_cleanup_old_databases(self, sqlite_driver, temp_db_dir):
        """Test that old database files are deleted."""
        now = datetime.now()
        old_date = now - timedelta(days=120)
        old_path = os.path.join(
            temp_db_dir, f"test_syslog_{old_date.strftime('%Y%m')}.sqlite3"
        )
        with open(old_path, "w") as f:
            f.write("test")

        recent_path = os.path.join(
            temp_db_dir, f"test_syslog_{now.strftime('%Y%m')}.sqlite3"
        )
        with open(recent_path, "w") as f:
            f.write("test")

        await sqlite_driver.cleanup_old_databases()

        assert not os.path.exists(old_path)
        assert os.path.exists(recent_path)

    @pytest.mark.asyncio
    async def test_cleanup_removes_wal_and_shm(
        self, sqlite_driver, temp_db_dir
    ):
        """Test that WAL and SHM files are also deleted."""
        sqlite_driver.retention_months = 0
        old_date = datetime.now() - timedelta(days=120)
        old_path = os.path.join(
            temp_db_dir, f"test_syslog_{old_date.strftime('%Y%m')}.sqlite3"
        )
        wal_path = old_path + "-wal"
        shm_path = old_path + "-shm"

        for path in [old_path, wal_path, shm_path]:
            with open(path, "w") as f:
                f.write("test")

        await sqlite_driver.cleanup_old_databases()

        assert not os.path.exists(old_path)
        assert not os.path.exists(wal_path)
        assert not os.path.exists(shm_path)

    @pytest.mark.asyncio
    async def test_connect_triggers_cleanup(self, sqlite_driver, temp_db_dir):
        """Test that connect() calls cleanup on startup."""
        sqlite_driver.retention_months = 0
        old_date = datetime.now() - timedelta(days=120)
        old_path = os.path.join(
            temp_db_dir, f"test_syslog_{old_date.strftime('%Y%m')}.sqlite3"
        )
        with open(old_path, "w") as f:
            f.write("test")

        await sqlite_driver.connect()

        assert not os.path.exists(old_path)

    @pytest.mark.asyncio
    async def test_close_handles_no_active_connection(self, sqlite_driver):
        """Test that close() works even when there's no active connection."""
        # Should not raise an exception when closing without active connection
        await sqlite_driver.close()

        # Connection should be None after close
        assert sqlite_driver.db is None

    @pytest.mark.asyncio
    async def test_retention_boundary(self, sqlite_driver, temp_db_dir):
        """Test that files within retention period are kept."""
        now = datetime.now()
        recent_date = now - timedelta(days=60)
        recent_path = os.path.join(
            temp_db_dir, f"test_syslog_{recent_date.strftime('%Y%m')}.sqlite3"
        )
        with open(recent_path, "w") as f:
            f.write("test")

        very_old_date = now - timedelta(days=150)
        very_old_path = os.path.join(
            temp_db_dir, f"test_syslog_{very_old_date.strftime('%Y%m')}.sqlite3"
        )
        with open(very_old_path, "w") as f:
            f.write("test")

        await sqlite_driver.cleanup_old_databases()

        assert os.path.exists(recent_path)
        assert not os.path.exists(very_old_path)

    @pytest.mark.asyncio
    async def test_switch_db_triggers_cleanup(self, sqlite_driver, temp_db_dir):
        """Test that switching to a new month triggers cleanup."""
        # Create an old database file
        old_date = datetime.now() - timedelta(days=150)
        old_path = os.path.join(
            temp_db_dir, f"test_syslog_{old_date.strftime('%Y%m')}.sqlite3"
        )
        with open(old_path, "w") as f:
            f.write("test")

        # Create a recent database file
        recent_date = datetime.now() - timedelta(days=30)
        recent_path = os.path.join(
            temp_db_dir, f"test_syslog_{recent_date.strftime('%Y%m')}.sqlite3"
        )
        with open(recent_path, "w") as f:
            f.write("test")

        # Simulate switching to a new month (which triggers cleanup)
        new_month_date = datetime.now().replace(day=1)
        await sqlite_driver._switch_db_if_needed(new_month_date)

        # Old file should be deleted, recent file should remain
        assert not os.path.exists(old_path)
        assert os.path.exists(recent_path)
