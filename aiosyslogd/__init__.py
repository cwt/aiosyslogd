from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("aiosyslogd")
except PackageNotFoundError:
    # Handle case where package is not installed (e.g., in development)
    __version__ = "0.0.0-dev"


from .priority import SyslogMatrix
from .server import SyslogUDPServer

__all__: list[str] = ["SyslogMatrix", "SyslogUDPServer"]
