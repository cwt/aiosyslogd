from unittest.mock import patch, MagicMock
import pytest
import sys

from aiosyslogd.activity import (
    AppActivity,
    AppcatGroup,
    ActivityResult,
    ActivityReport,
)
from aiosyslogd.cli_activity import _make_table, _render_report, main


class TestMakeTable:
    def test_single_user_single_appcat(self):
        user = ActivityResult(
            user="alice",
            appcats=[
                AppcatGroup(
                    appcat="Video",
                    apps=[AppActivity(app="YouTube", minutes=42)],
                    total_minutes=42,
                )
            ],
            total_minutes=42,
        )
        table = _make_table([user])
        output = table.get_string()
        assert "alice" in output
        assert "YouTube" in output
        assert "Video" in output

    def test_single_user_multiple_appcats(self):
        user = ActivityResult(
            user="bob",
            appcats=[
                AppcatGroup(
                    appcat="Email",
                    apps=[AppActivity(app="Gmail", minutes=10)],
                    total_minutes=10,
                ),
                AppcatGroup(
                    appcat="Video",
                    apps=[AppActivity(app="YouTube", minutes=5)],
                    total_minutes=5,
                ),
            ],
            total_minutes=15,
        )
        table = _make_table([user])
        output = table.get_string()
        assert "Email" in output
        assert "Video" in output
        assert "Gmail" in output
        assert "YouTube" in output

    def test_multiple_users(self):
        users = [
            ActivityResult(
                user="alice",
                appcats=[
                    AppcatGroup(
                        appcat="Video",
                        apps=[AppActivity(app="YouTube", minutes=42)],
                        total_minutes=42,
                    )
                ],
                total_minutes=42,
            ),
            ActivityResult(
                user="bob",
                appcats=[
                    AppcatGroup(
                        appcat="Email",
                        apps=[AppActivity(app="Gmail", minutes=10)],
                        total_minutes=10,
                    )
                ],
                total_minutes=10,
            ),
        ]
        table = _make_table(users)
        output = table.get_string()
        assert "alice" in output
        assert "bob" in output
        assert "42m" in output
        assert "10m" in output


def _make_report(users, total_logs=100, query_time=1.5, window_mins=1440):
    return ActivityReport(
        timeframe={"from": "", "to": ""},
        total_window_minutes=window_mins,
        users=users,
        total_logs=total_logs,
        query_time=query_time,
    )


class TestRenderReport:
    def test_no_users(self):
        report = _make_report([], total_logs=0)
        output = _render_report(report)
        assert "No user activity found" in output

    def test_with_users_single_column(self, monkeypatch):
        def mock_terminal_size(*a, **kw):
            return (80, 24)

        monkeypatch.setattr(
            "shutil.get_terminal_size",
            mock_terminal_size,
        )
        user = ActivityResult(
            user="alice",
            appcats=[
                AppcatGroup(
                    appcat="Video",
                    apps=[AppActivity(app="YouTube", minutes=42)],
                    total_minutes=42,
                )
            ],
            total_minutes=42,
        )
        report = _make_report([user])
        output = _render_report(report)
        assert "alice" in output
        assert "100 matching logs" in output
        assert "1.500s" in output
        assert "window: 1440" in output

    def test_with_users_dual_column(self, monkeypatch):
        def mock_terminal_size(*a, **kw):
            return (200, 24)

        monkeypatch.setattr(
            "shutil.get_terminal_size",
            mock_terminal_size,
        )
        users = [
            ActivityResult(
                user="alice",
                appcats=[
                    AppcatGroup(
                        appcat="Video",
                        apps=[AppActivity(app="YouTube", minutes=42)],
                        total_minutes=42,
                    )
                ],
                total_minutes=42,
            ),
            ActivityResult(
                user="bob",
                appcats=[
                    AppcatGroup(
                        appcat="Email",
                        apps=[AppActivity(app="Gmail", minutes=10)],
                        total_minutes=10,
                    )
                ],
                total_minutes=10,
            ),
        ]
        report = _make_report(users)
        output = _render_report(report)
        assert "alice" in output
        assert "bob" in output


class TestMain:
    def test_required_args(self):
        test_args = ["prog", "-d", "test.db", "-q", "test query"]
        with patch.object(sys, "argv", test_args):
            with patch(
                "aiosyslogd.cli_activity.run_activity_report"
            ) as mock_run:
                mock_report = MagicMock()
                mock_report.error = None
                mock_report.users = []
                mock_report.total_logs = 0
                mock_report.query_time = 0.1
                mock_report.total_window_minutes = 60
                mock_run.return_value = mock_report
                main()
                mock_run.assert_called_once()

    def test_passes_filters_correctly(self):
        test_args = [
            "prog",
            "-d",
            "test.db",
            "-q",
            "test",
            "--from",
            "2026-04-01",
            "--to",
            "2026-04-02",
            "--parser",
            "fortios",
        ]
        mock_report = ActivityReport(
            timeframe={},
            total_window_minutes=60,
            users=[
                ActivityResult(
                    user="alice",
                    appcats=[
                        AppcatGroup(
                            appcat="Video",
                            apps=[AppActivity(app="YouTube", minutes=10)],
                            total_minutes=10,
                        )
                    ],
                    total_minutes=10,
                )
            ],
            total_logs=1,
            query_time=0.1,
        )
        with patch.object(sys, "argv", test_args):
            with patch(
                "aiosyslogd.cli_activity.run_activity_report",
                return_value=mock_report,
            ) as mock_run:
                main()
                _, kwargs = mock_run.call_args
                assert kwargs["db_path"] == "test.db"
                assert kwargs["search_query"] == "test"
                assert kwargs["filters"]["received_at_min"] == "2026-04-01"
                assert kwargs["filters"]["received_at_max"] == "2026-04-02"

    def test_error_exit_code(self):
        test_args = ["prog", "-d", "test.db", "-q", "test"]
        with patch.object(sys, "argv", test_args):
            with patch(
                "aiosyslogd.cli_activity.run_activity_report"
            ) as mock_run:
                mock_report = MagicMock()
                mock_report.error = "Fatal error"
                mock_run.return_value = mock_report
                with pytest.raises(SystemExit) as e:
                    main()
                assert e.value.code == 1
