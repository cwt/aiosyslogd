# -*- coding: utf-8 -*-
from importlib.metadata import version, PackageNotFoundError
from typing import List

try:
    __version__: str = version("aiosyslogd")
except PackageNotFoundError:
    # Handle case where package is not installed (e.g., in development)
    __version__ = "0.0.0-dev"


from .server import SyslogUDPServer
from .priority import SyslogMatrix

__all__: List[str] = ["SyslogUDPServer", "SyslogMatrix"]
