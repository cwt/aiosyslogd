#!/usr/bin/env python
# -*- coding: utf-8 -*-
## Syslog Server in Python with asyncio and pluggable DB drivers.

from . import config
from .db import BaseDatabase
from .priority import SyslogMatrix
from .rfc5424 import RFC5424_PATTERN, normalize_to_rfc5424
from datetime import datetime
from importlib import import_module
from types import ModuleType
from typing import Dict, Any, Tuple, List, Type, Self
import asyncio
import re
import signal
import sys

uvloop: ModuleType | None = None
try:
    if sys.platform == "win32":
        import winloop as uvloop
    else:
        import uvloop
except ImportError:
    pass  # uvloop or winloop is an optional for speedup, not a requirement

# --- Configuration ---
CFG = config.load_config()

# Server settings
DEBUG: bool = CFG.get("server", {}).get("debug", False)
LOG_DUMP: bool = CFG.get("server", {}).get("log_dump", False)
BINDING_IP: str = CFG.get("server", {}).get("bind_ip", "0.0.0.0")
BINDING_PORT: int = int(CFG.get("server", {}).get("bind_port", 5140))

# Database settings
DB_CFG = CFG.get("database", {})
DB_DRIVER: str = DB_CFG.get("driver", "sqlite")
BATCH_SIZE: int = int(DB_CFG.get("batch_size", 1000))
BATCH_TIMEOUT: int = int(DB_CFG.get("batch_timeout", 5))


# --- Security: Define an allowlist of valid database drivers ---
ALLOWED_DB_DRIVERS = {"sqlite", "meilisearch"}


def get_db_driver() -> BaseDatabase | None:
    """Dynamically imports and returns a database driver instance."""
    if DB_DRIVER is None:
        return None
    # --- SECURITY MITIGATION ---
    # Validate the driver name against the allowlist to prevent code injection.
    if DB_DRIVER not in ALLOWED_DB_DRIVERS:
        print(
            f"Error: Invalid database driver '{DB_DRIVER}' specified in configuration."
        )
        print(f"Allowed drivers are: {', '.join(ALLOWED_DB_DRIVERS)}")
        raise SystemExit("Aborting due to invalid database driver.")
    # --- END SECURITY MITIGATION ---
    try:
        driver_module = import_module(f".db.{DB_DRIVER}", package="aiosyslogd")
        driver_class = getattr(driver_module, f"{DB_DRIVER.capitalize()}Driver")
        driver_config = DB_CFG.get(DB_DRIVER, {})
        # Pass general db settings to the driver as well
        driver_config["debug"] = DEBUG
        driver_config["sql_dump"] = DB_CFG.get("sql_dump", False)
        return driver_class(driver_config)
    except (ImportError, AttributeError) as e:
        print(f"Error loading database driver '{DB_DRIVER}': {e}")
        raise SystemExit("Aborting due to invalid database driver.")


class SyslogUDPServer(asyncio.DatagramProtocol):
    """An asynchronous Syslog UDP server with pluggable DB drivers."""

    syslog_matrix: SyslogMatrix = SyslogMatrix()

    def __init__(
        self, host: str, port: int, db_driver: BaseDatabase | None
    ) -> None:
        """Initializes the SyslogUDPServer instance."""
        self.host: str = host
        self.port: int = port
        self.db = db_driver
        self.loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self.transport: asyncio.DatagramTransport | None = None
        self._shutting_down: bool = False
        self._db_writer_task: asyncio.Task[None] | None = None
        self._message_queue: asyncio.Queue[
            Tuple[bytes, Tuple[str, int], datetime]
        ] = asyncio.Queue()

    @classmethod
    async def create(cls: Type[Self], host: str, port: int) -> Self:
        """Creates and initializes the SyslogUDPServer instance."""
        db_driver = get_db_driver()
        server = cls(host, port, db_driver)
        print(f"aiosyslogd starting on UDP {host}:{port}...")
        if server.db:
            print(f"Using '{DB_DRIVER}' database driver.")
            print(f"Batch size: {BATCH_SIZE}, Timeout: {BATCH_TIMEOUT}s")
            await server.db.connect()
        if DEBUG:
            print("Debug mode is ON.")
        return server

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Handles the connection made event."""
        self.transport = transport  # type: ignore
        if self.db and not self._db_writer_task:
            self._db_writer_task = self.loop.create_task(self.database_writer())
            print("Database writer task started.")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Quickly queue incoming messages without processing."""
        if self._shutting_down:
            return
        self._message_queue.put_nowait((data, addr, datetime.now()))

    def error_received(self, exc: Exception) -> None:
        if DEBUG:
            print(f"Error received: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        if DEBUG:
            print(f"Connection lost: {exc}")

    async def database_writer(self) -> None:
        """A dedicated task to write messages to the database in batches."""
        batch: List[Dict[str, Any]] = []
        while not self._shutting_down:
            try:
                data, addr, received_at = await asyncio.wait_for(
                    self._message_queue.get(), timeout=BATCH_TIMEOUT
                )
                params = self.process_datagram(data, addr, received_at)
                if params:
                    batch.append(params)
                self._message_queue.task_done()
                if len(batch) >= BATCH_SIZE:
                    if self.db:
                        await self.db.write_batch(batch)
                    batch.clear()
            except asyncio.TimeoutError:
                if batch and self.db:
                    await self.db.write_batch(batch)
                batch.clear()
            except asyncio.CancelledError:
                break
            except Exception as e:
                if DEBUG:
                    print(f"[DB-WRITER-ERROR] {e}")
        if batch and self.db:
            await self.db.write_batch(batch)  # Final write
        print("Database writer task finished.")

    def process_datagram(
        self, data: bytes, address: Tuple[str, int], received_at: datetime
    ) -> Dict[str, Any] | None:
        """Processes a single datagram and returns a dictionary of params for DB insert."""
        try:
            decoded_data: str = data.decode("utf-8")
        except UnicodeDecodeError:
            if DEBUG:
                print(f"Cannot decode message from {address}: {data!r}")
            return None

        processed_data: str = normalize_to_rfc5424(
            decoded_data, debug_mode=DEBUG
        )
        if LOG_DUMP:
            print(
                f"\n[{received_at}] FROM {address[0]}:\n  RFC5424 DATA: {processed_data}"
            )

        match: re.Match[str] | None = RFC5424_PATTERN.match(processed_data)
        if not match:
            if DEBUG:
                print(f"Failed to parse as RFC-5424: {processed_data}")
            pri_end: int = processed_data.find(">")
            code: str = processed_data[1:pri_end] if pri_end != -1 else "14"
            Facility, Priority = self.syslog_matrix.decode_int(code)
            return {
                "Facility": Facility,
                "Priority": Priority,
                "FromHost": address[0],
                "InfoUnitID": 1,
                "ReceivedAt": received_at,
                "DeviceReportedTime": received_at,
                "SysLogTag": "UNKNOWN",
                "ProcessID": "0",
                "Message": processed_data,
            }

        parts: Dict[str, Any] = match.groupdict()
        try:
            ts_str: str = parts["ts"].upper().replace("Z", "+00:00")
            device_reported_time = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            device_reported_time = received_at

        return {
            "Facility": self.syslog_matrix.decode_int(parts["pri"])[0],
            "Priority": self.syslog_matrix.decode_int(parts["pri"])[1],
            "FromHost": parts["host"] if parts["host"] != "-" else address[0],
            "InfoUnitID": 1,
            "ReceivedAt": received_at,
            "DeviceReportedTime": device_reported_time,
            "SysLogTag": parts["app"] if parts["app"] != "-" else "UNKNOWN",
            "ProcessID": parts["pid"] if parts["pid"] != "-" else "0",
            "Message": parts["msg"].strip(),
        }

    async def shutdown(self) -> None:
        """Gracefully shuts down the server."""
        print("\nShutting down server...")
        self._shutting_down = True
        if self.transport:
            self.transport.close()
        if self._db_writer_task:
            self._db_writer_task.cancel()
            await self._db_writer_task
        if self.db:
            await self.db.close()


async def run_server() -> None:
    """Sets up and runs the server until a shutdown signal is received."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    server: SyslogUDPServer = await SyslogUDPServer.create(
        host=BINDING_IP, port=BINDING_PORT
    )

    def protocol_factory() -> SyslogUDPServer:
        return server

    transport, _ = await loop.create_datagram_endpoint(
        protocol_factory, local_addr=(server.host, server.port)
    )
    print(f"Server is running. Press Ctrl+C to stop.")

    try:
        if sys.platform == "win32":
            await asyncio.Future()
        else:
            stop_event = asyncio.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)
            await stop_event.wait()
    finally:
        print("\nShutdown signal received.")
        transport.close()
        await server.shutdown()


def main() -> None:
    """CLI Entry point."""
    if uvloop:
        print("Using uvloop for the event loop.")
        uvloop.install()
    try:
        asyncio.run(run_server())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("Server has been shut down.")


if __name__ == "__main__":
    main()
