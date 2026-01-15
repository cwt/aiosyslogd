from unittest.mock import patch, mock_open, MagicMock
import pytest
import toml

# --- Import the module and constants to be tested ---
from aiosyslogd.config import (
    load_config,
    DEFAULT_CONFIG,
)


# --- Test Suite for config.py ---


class TestConfigLoading:
    """Tests for the configuration loading logic in aiosyslogd.config."""

    @patch("aiosyslogd.config.os.environ.get", return_value=None)
    @patch("aiosyslogd.config.open", new_callable=mock_open)
    @patch("aiosyslogd.config.toml.dump")
    def test_create_default_config_when_missing(
        self, mock_toml_dump, mock_file_open, mock_env_get
    ):
        """
        Tests that a default config file is created if it doesn't exist.
        """
        # --- Arrange ---
        # Create a mock file handle that we can reference later.
        mock_handle = MagicMock()

        # The first call to open() (read mode) will fail.
        # The second call to open() (write mode) will succeed, returning our pre-made handle.
        mock_file_open.side_effect = [
            FileNotFoundError,
            mock_handle,
        ]

        # --- Act ---
        loaded_cfg = load_config()

        # --- Assert ---
        # Verify the sequence of calls to open()
        assert mock_file_open.call_count == 2
        mock_file_open.assert_any_call("aiosyslogd.toml", "r")
        mock_file_open.assert_any_call("aiosyslogd.toml", "w")

        # The 'with' statement calls __enter__ on the mock handle.
        # So we need to assert against the return value of that.
        mock_toml_dump.assert_called_once_with(
            DEFAULT_CONFIG, mock_handle.__enter__()
        )

        # Verify the loaded config is the default one
        assert loaded_cfg == DEFAULT_CONFIG

    @patch("aiosyslogd.config.os.environ.get", return_value=None)
    def test_load_existing_default_config(self, mock_env_get):
        """
        Tests loading an existing 'aiosyslogd.toml' from the current directory.
        """
        # --- Arrange ---
        mock_toml_content = """
[server]
bind_ip = "127.0.0.1"
bind_port = 5141
"""
        # Simulate reading the mock TOML content from the file
        m = mock_open(read_data=mock_toml_content)
        with patch("aiosyslogd.config.open", m):
            # --- Act ---
            loaded_cfg = load_config()

            # --- Assert ---
            m.assert_called_once_with("aiosyslogd.toml", "r")
            assert loaded_cfg["server"]["bind_ip"] == "127.0.0.1"
            assert loaded_cfg["server"]["bind_port"] == 5141

    @patch(
        "aiosyslogd.config.os.environ.get",
        return_value="/etc/custom/config.toml",
    )
    def test_load_config_from_env_variable(self, mock_env_get):
        """
        Tests loading a configuration from a path specified in an environment variable.
        """
        # --- Arrange ---
        mock_toml_content = """
[database]
driver = "meilisearch"
"""
        m = mock_open(read_data=mock_toml_content)
        with patch("aiosyslogd.config.open", m):
            # --- Act ---
            loaded_cfg = load_config()

            # --- Assert ---
            mock_env_get.assert_called_once_with("AIOSYSLOGD_CONFIG")
            m.assert_called_once_with("/etc/custom/config.toml", "r")
            assert loaded_cfg["database"]["driver"] == "meilisearch"

    @patch(
        "aiosyslogd.config.os.environ.get",
        return_value="/etc/nonexistent/config.toml",
    )
    def test_load_missing_custom_config_raises_sysexit(self, mock_env_get):
        """
        Tests that the program exits if a custom config path is provided but the file is missing.
        """
        # --- Arrange ---
        m = mock_open()
        m.side_effect = FileNotFoundError
        with patch("aiosyslogd.config.open", m):
            # --- Act & Assert ---
            with pytest.raises(SystemExit) as e:
                load_config()
            # Check that the exit code is non-zero (typically 1)
            assert e.type is SystemExit
            assert e.value.code is not None and e.value.code != 0

    @patch("aiosyslogd.config.os.environ.get", return_value=None)
    def test_load_invalid_toml_raises_sysexit(self, mock_env_get):
        """
        Tests that the program exits if the configuration file contains invalid TOML.
        """
        # --- Arrange ---
        # Simulate reading a file with invalid content
        m = mock_open(read_data="this is not valid toml")
        with patch("aiosyslogd.config.open", m):
            # Simulate the toml library failing to parse
            with patch(
                "aiosyslogd.config.toml.load",
                side_effect=toml.TomlDecodeError("Invalid TOML", "", 0),
            ):
                # --- Act & Assert ---
                with pytest.raises(SystemExit) as e:
                    load_config()
                assert e.type is SystemExit
                assert e.value.code is not None and e.value.code != 0
