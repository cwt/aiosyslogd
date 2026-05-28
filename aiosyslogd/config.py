# aiosyslogd/config.py
# -*- coding: utf-8 -*-
from loguru import logger
from typing import Any, Dict
import os
import sys
import toml

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
        "batch_size": 100,
        "batch_timeout": 5,
        "sqlite": {
            "database": "syslog.sqlite3",
            "retention_months": 12,
            "sql_dump": False,
            "debug": False,
        },
        "meilisearch": {
            "url": "http://127.0.0.1:7700",
            "api_key": "",
            "debug": False,
        },
    },
    "web_server": {
        "bind_ip": "0.0.0.0",
        "bind_port": 5141,
        "debug": False,
        "redact": False,
        "users_file": "users.json",
    },
    "activity": {
        "parser": "fortios",
    },
}

DEFAULT_CONFIG_FILENAME = "aiosyslogd.toml"
# Configure the logger early to ensure all logs are formatted consistently.
logger.remove()
logger.add(
    sys.stderr,
    format="[{time:YYYY-MM-DD HH:mm:ss ZZ}] [{process}] [{level}] {message}",
    level="INFO",  # Since this is a library, we default to INFO level logging.
)

_CONFIG_CACHE: Dict[str, Any] | None = None


def _create_default_config(path: str) -> Dict[str, Any]:
    """Creates the default aiosyslogd.toml file at the given path."""
    logger.info(f"Configuration file not found. Creating a default '{path}'...")
    with open(path, "w") as f:
        toml.dump(DEFAULT_CONFIG, f)
    logger.info(
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

    The result is cached; subsequent calls return the cached config without re-reading
    the file or re-logging the load message.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_path_from_env: str | None = os.environ.get("AIOSYSLOGD_CONFIG")

    if config_path_from_env:
        config_path: str = config_path_from_env
        is_custom_path: bool = True
    else:
        config_path = DEFAULT_CONFIG_FILENAME
        is_custom_path = False

    logger.info(f"Attempting to load configuration from: {config_path}")

    try:
        with open(config_path, "r") as f:
            _CONFIG_CACHE = toml.load(f)
    except FileNotFoundError:
        if is_custom_path:
            logger.error(
                f"Configuration file not found at the specified path: {config_path}"
            )
            raise SystemExit(
                "Aborting: Could not find the specified configuration file."
            )
        else:
            _CONFIG_CACHE = _create_default_config(config_path)
    except toml.TomlDecodeError as e:
        logger.error(f"Error decoding TOML file {config_path}: {e}")
        raise SystemExit("Aborting due to invalid configuration file.")
    return _CONFIG_CACHE
