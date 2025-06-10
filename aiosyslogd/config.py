# aiosyslogd/config.py
# -*- coding: utf-8 -*-
import os
import toml
from typing import Any, Dict

# --- Default Configuration ---
DEFAULT_CONFIG: Dict[str, Any] = {
    "server": {
        "bind_ip": "0.0.0.0",
        "bind_port": 5140,
        "debug": False,
        "log_dump": False,
    },
    "database": {
        "driver": "sqlite",  # sqlite is the default driver
        "batch_size": 1000,
        "batch_timeout": 5,
        "sql_dump": False,
        "sqlite": {
            "database": "syslog.sqlite3",
        },
        "meilisearch": {
            "url": "http://127.0.0.1:7700",
            "api_key": None,
        },
    },
}

DEFAULT_CONFIG_FILENAME = "aiosyslogd.toml"


def _create_default_config(path: str) -> Dict[str, Any]:
    """Creates the default aiosyslogd.toml file at the given path."""
    print(f"Configuration file not found. Creating a default '{path}'...")
    with open(path, "w") as f:
        toml.dump(DEFAULT_CONFIG, f)
    print(
        f"Default configuration file created. Please review '{path}' "
        "and restart the server if needed."
    )
    return DEFAULT_CONFIG


def load_config() -> Dict[str, Any]:
    """
    Loads configuration from a TOML file.

    It first checks for the 'AIOSYSLOGD_CONFIG' environment variable for a custom path.
    If the variable is not set, it falls back to 'aiosyslogd.toml' in the current directory.

    - If a custom path is specified and the file doesn't exist,
      the server will exit with an error.
    - If the default file ('aiosyslogd.toml') doesn't exist,
      it will be created automatically.
    """
    config_path_from_env: str | None = os.environ.get("AIOSYSLOGD_CONFIG")

    if config_path_from_env:
        config_path: str = config_path_from_env
        is_custom_path: bool = True
    else:
        config_path = DEFAULT_CONFIG_FILENAME
        is_custom_path = False

    print(f"Attempting to load configuration from: {config_path}")

    try:
        with open(config_path, "r") as f:
            return toml.load(f)
    except FileNotFoundError:
        if is_custom_path:
            # If a custom path was provided and it doesn't exist, it's an error.
            print(
                f"Error: Configuration file not found at the specified path: {config_path}"
            )
            raise SystemExit(
                "Aborting: Could not find the specified configuration file."
            )
        else:
            # If the default file is not found, create it.
            return _create_default_config(config_path)
    except toml.TomlDecodeError as e:
        print(f"Error decoding TOML file {config_path}: {e}")
        raise SystemExit("Aborting due to invalid configuration file.")
