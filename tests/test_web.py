from unittest.mock import patch
import pytest
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
