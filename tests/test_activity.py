import pytest
import pytest_asyncio
import aiosqlite
import sqlite3

from aiosyslogd.activity import run_activity_report
from aiosyslogd.activity import ActivityReport
from aiosyslogd.activity.parsers import (
    BaseActivityParser,
    ParsedActivity,
)


def _setup_schema_sql() -> list:
    return [
        """CREATE TABLE SystemEvents (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Facility INTEGER,
            Priority INTEGER,
            FromHost TEXT,
            InfoUnitID INTEGER,
            ReceivedAt TIMESTAMP,
            DeviceReportedTime TIMESTAMP,
            SysLogTag TEXT,
            ProcessID TEXT,
            Message TEXT
        )""",
        """CREATE INDEX idx_SystemEvents_ReceivedAt ON SystemEvents (ReceivedAt)""",
        """CREATE VIRTUAL TABLE SystemEvents_FTS
           USING fts5(Message, content='SystemEvents', content_rowid='ID')""",
        """CREATE TRIGGER SystemEvents_insert AFTER INSERT ON SystemEvents
           BEGIN
               INSERT INTO SystemEvents_FTS(rowid, Message) VALUES (new.ID, new.Message);
           END""",
        """CREATE TRIGGER SystemEvents_update AFTER UPDATE ON SystemEvents
           BEGIN
               UPDATE SystemEvents_FTS SET Message = new.Message WHERE rowid = new.ID;
           END""",
        """CREATE TRIGGER SystemEvents_delete AFTER DELETE ON SystemEvents
           BEGIN
               DELETE FROM SystemEvents_FTS WHERE rowid = old.ID;
           END""",
    ]


async def _setup_db(db_path):
    async with aiosqlite.connect(db_path) as conn:
        for sql in _setup_schema_sql():
            await conn.execute(sql)
        await conn.commit()


async def _insert_log(conn, message, received_at, from_host="testhost"):
    await conn.execute(
        "INSERT INTO SystemEvents (Message, ReceivedAt, FromHost, SysLogTag) "
        "VALUES (?, ?, ?, ?)",
        (message, received_at, from_host, "test"),
    )
    await conn.commit()


@pytest_asyncio.fixture
async def db_path(tmp_path):
    db = tmp_path / "test_activity.sqlite3"
    await _setup_db(str(db))
    yield str(db)


@pytest_asyncio.fixture
async def populated_db(db_path):
    async with aiosqlite.connect(
        db_path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    ) as conn:
        base = "2026-04-15 12:00:00"
        await _insert_log(
            conn,
            'app="YouTube" user="alice" appcat="Video" srcip 10.100.141.1',
            base,
        )
        await _insert_log(
            conn,
            'app="YouTube" user="alice" appcat="Video" srcip 10.100.141.2',
            "2026-04-15 12:01:00",
        )
        await _insert_log(
            conn,
            'app="Facebook" user="alice" appcat="Social" srcip 10.100.141.3',
            "2026-04-15 12:02:00",
        )
        await _insert_log(
            conn,
            'app="Gmail" user="bob" appcat="Email" srcip 10.100.141.4',
            "2026-04-15 12:05:00",
        )
        await _insert_log(
            conn,
            'app="Gmail" user="bob" appcat="Email" srcip 10.100.141.5',
            "2026-04-15 12:05:00",
        )
        await _insert_log(
            conn,
            "log with no user or app fields here",
            "2026-04-15 12:10:00",
        )
        await _insert_log(
            conn,
            None,
            "2026-04-15 12:15:00",
        )
    return db_path


class TestRunActivityReportFullScan:
    @pytest.mark.asyncio
    async def test_full_scan_basic_aggregation(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={},
        )
        assert isinstance(report, ActivityReport)
        assert report.error is None

        usernames = [u.user for u in report.users]
        assert usernames == ["alice", "bob"]

        alice = report.users[0]
        assert alice.total_minutes == 3
        assert len(alice.appcats) == 2

        bob = report.users[1]
        assert bob.total_minutes == 1
        assert len(bob.appcats) == 1

        expected_appcats = {"Video", "Social"}
        actual_appcats = {g.appcat for g in alice.appcats}
        assert actual_appcats == expected_appcats

    @pytest.mark.asyncio
    async def test_full_scan_with_fts5_query(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db,
            search_query='"srcip 10 100"',
            filters={},
        )
        assert report.error is None
        assert report.total_logs > 0
        for u in report.users:
            for g in u.appcats:
                for a in g.apps:
                    assert a.app in ("YouTube", "Facebook", "Gmail")

    @pytest.mark.asyncio
    async def test_full_scan_with_time_filter(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={
                "received_at_min": "2026-04-15 12:00",
                "received_at_max": "2026-04-15 12:03",
            },
        )
        assert report.error is None
        users = [u.user for u in report.users]
        assert users == ["alice"]
        alice = report.users[0]
        assert alice.total_minutes == 3

    @pytest.mark.asyncio
    async def test_full_scan_with_from_host_filter(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={"from_host": "testhost"},
        )
        assert report.error is None
        assert len(report.users) == 2

        report2 = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={"from_host": "nonexistent"},
        )
        assert report2.error is None
        assert len(report2.users) == 0

    @pytest.mark.asyncio
    async def test_swapped_time_filters(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={
                "received_at_min": "2026-04-15 12:05",
                "received_at_max": "2026-04-15 12:00",
            },
        )
        assert report.error is None
        assert report.total_logs > 0

    @pytest.mark.asyncio
    async def test_empty_database(self, db_path):
        report = await run_activity_report(
            db_path=db_path,
            search_query="",
            filters={},
        )
        assert report.error is None
        assert report.users == []
        assert report.total_logs == 0

    @pytest.mark.asyncio
    async def test_custom_parser(self, populated_db):
        class CountingParser(BaseActivityParser):
            def __init__(self):
                self.call_count = 0

            def extract(self, message):
                self.call_count += 1
                if not message:
                    return None
                if "extra_user" in message:
                    return ParsedActivity(
                        app="TestApp", user="extra", appcat="Test"
                    )
                if "user=" not in message:
                    return None
                if 'app="YouTube"' in message:
                    return ParsedActivity(
                        app="YouTube", user="alice2", appcat="Video"
                    )
                if 'app="Facebook"' in message:
                    return ParsedActivity(
                        app="Facebook", user="alice2", appcat="Social"
                    )
                if 'app="Gmail"' in message:
                    return ParsedActivity(
                        app="Gmail", user="bob2", appcat="Email"
                    )
                return None

        cp = CountingParser()
        report = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={},
            parser=cp,
        )
        assert cp.call_count > 0
        usernames = [u.user for u in report.users]
        assert "alice2" in usernames
        assert "bob2" in usernames

    @pytest.mark.asyncio
    async def test_invalid_time_filter_falls_back(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={
                "received_at_min": "not-a-date",
                "received_at_max": "also-invalid",
            },
        )
        assert report.error is None
        assert report.total_logs >= 5
        assert len(report.users) >= 2

    @pytest.mark.asyncio
    async def test_report_timeframe_fields(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db,
            search_query="",
            filters={
                "received_at_min": "2026-04-15 00:00",
                "received_at_max": "2026-04-16 00:00",
            },
        )
        assert report.timeframe["from"] == "2026-04-15 00:00"
        assert report.timeframe["to"] == "2026-04-16 00:00"
        assert report.total_window_minutes >= 1
        assert report.query_time > 0

    @pytest.mark.asyncio
    async def test_users_sorted_by_total_minutes_desc(self, populated_db):
        report = await run_activity_report(
            db_path=populated_db, search_query="", filters={}
        )
        totals = [u.total_minutes for u in report.users]
        assert totals == sorted(totals, reverse=True)

    @pytest.mark.asyncio
    async def test_malformed_fts5_query_returns_error_report(
        self, populated_db
    ):
        report = await run_activity_report(
            db_path=populated_db, search_query="app:YouTube OR", filters={}
        )
        assert report.error is not None
        assert report.total_logs == 0
        assert report.users == []
