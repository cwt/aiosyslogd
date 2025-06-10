#!/usr/bin/env python
# -*- coding: utf-8 -*-
## Syslog Server in Python with asyncio and SQLite.

from . import config
from .priority import SyslogMatrix
from .rfc5424 import RFC5424_PATTERN
from .rfc5424 import normalize_to_rfc5424
from datetime import datetime
from types import ModuleType
from typing import Dict, Any, Tuple, List, Type, Self
import aiosqlite
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
# Load configuration from aiosyslogd.toml
CFG = config.load_config()

# Server settings
DEBUG: bool = CFG.get("server", {}).get("debug", False)
LOG_DUMP: bool = CFG.get("server", {}).get("log_dump", False)
BINDING_IP: str = CFG.get("server", {}).get("bind_ip", "0.0.0.0")
BINDING_PORT: int = int(CFG.get("server", {}).get("bind_port", 5140))

# SQLite settings
SQL_WRITE: bool = CFG.get("sqlite", {}).get("enabled", False)
SQL_DUMP: bool = CFG.get("sqlite", {}).get("sql_dump", False)
SQLITE_DB_PATH: str = CFG.get("sqlite", {}).get("database", "syslog.sqlite3")
BATCH_SIZE: int = int(CFG.get("sqlite", {}).get("batch_size", 1000))
BATCH_TIMEOUT: int = int(CFG.get("sqlite", {}).get("batch_timeout", 5))


class SyslogUDPServer(asyncio.DatagramProtocol):
    """An asynchronous Syslog UDP server with batch database writing."""

    syslog_matrix: SyslogMatrix = SyslogMatrix()

    def __init__(self, host: str, port: int) -> None:
        """Initializes the SyslogUDPServer instance."""
        self.host: str = host
        self.port: int = port
        self.loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self.transport: asyncio.DatagramTransport | None = None
        self.db: aiosqlite.Connection | None = None
        self._shutting_down: bool = False
        self._db_writer_task: asyncio.Task[None] | None = None
        self._message_queue: asyncio.Queue[
            Tuple[bytes, Tuple[str, int], datetime]
        ] = asyncio.Queue()

    @classmethod
    async def create(cls: Type[Self], host: str, port: int) -> Self:
        """Creates and initializes the SyslogUDPServer instance."""
        server = cls(host, port)
        print(f"aiosyslogd starting on UDP {host}:{port}...")
        if SQL_WRITE:
            print(f"SQLite writing ENABLED to '{SQLITE_DB_PATH}.")
            print(f"Batch size: {BATCH_SIZE}, Timeout: {BATCH_TIMEOUT}s")
            await server.connect_to_sqlite()
        if DEBUG:
            print("Debug mode is ON.")
        return server

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Handles the connection made event."""
        self.transport = transport  # type: ignore
        if SQL_WRITE and not self._db_writer_task:
            self._db_writer_task = self.loop.create_task(self.database_writer())
            print("Database writer task started.")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Quickly queue incoming messages without processing."""
        if self._shutting_down:
            return
        self._message_queue.put_nowait((data, addr, datetime.now()))

    def error_received(self, exc: Exception) -> None:
        """Handles errors received from the transport."""
        if DEBUG:
            print(f"Error received: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        """Handles connection loss."""
        if DEBUG:
            print(f"Connection lost: {exc}")

    async def database_writer(self) -> None:
        """A dedicated task to write messages to the database in batches."""
        batch: List[Dict[str, Any]] = []
        async for data, addr, received_at in AsyncQueueIterator(
            self._message_queue
        ):
            try:
                params = self.process_datagram(data, addr, received_at)
                if params:
                    batch.append(params)
                self._message_queue.task_done()
                if len(batch) >= BATCH_SIZE:
                    await self.write_batch_to_db(batch)
                    batch.clear()
            except Exception as e:
                if DEBUG:
                    print(f"[DB-WRITER-ERROR] {e}")
            # Check for shutdown or empty queue to exit gracefully
            if self._shutting_down and self._message_queue.empty():
                break
        if batch:
            await self.write_batch_to_db(batch)
            batch.clear()
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

        if LOG_DUMP and not SQL_DUMP:
            print(
                f"\n[{received_at}] FROM {address[0]}:\n  RFC5424 DATA: {processed_data}"
            )

        try:
            match: re.Match[str] | None = RFC5424_PATTERN.match(processed_data)
            if not match:
                if DEBUG:
                    print(f"Failed to parse as RFC-5424: {processed_data}")
                pri_end: int = processed_data.find(">")
                code: str = processed_data[1:pri_end] if pri_end != -1 else "14"
                Facility, Priority = self.syslog_matrix.decode_int(code)
                FromHost, DeviceReportedTime = address[0], received_at
                SysLogTag, ProcessID, Message = "UNKNOWN", "0", processed_data
            else:
                parts: Dict[str, Any] = match.groupdict()
                code = parts["pri"]
                Facility, Priority = self.syslog_matrix.decode_int(code)
                try:
                    ts_str: str = parts["ts"].upper().replace("Z", "+00:00")
                    DeviceReportedTime = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    DeviceReportedTime = received_at
                FromHost = parts["host"] if parts["host"] != "-" else address[0]
                SysLogTag = parts["app"] if parts["app"] != "-" else "UNKNOWN"
                ProcessID = parts["pid"] if parts["pid"] != "-" else "0"
                Message = parts["msg"].strip()

            return {
                "Facility": Facility,
                "Priority": Priority,
                "FromHost": FromHost,
                "InfoUnitID": 1,
                "ReceivedAt": received_at,
                "DeviceReportedTime": DeviceReportedTime,
                "SysLogTag": SysLogTag,
                "ProcessID": ProcessID,
                "Message": Message,
            }
        except Exception as e:
            if DEBUG:
                print(
                    f"CRITICAL PARSE FAILURE on: {processed_data}\nError: {e}"
                )
            return None

    async def write_batch_to_db(self, batch: List[Dict[str, Any]]) -> None:
        """Writes a batch of messages to the database."""
        if not batch or (self._shutting_down and not self.db) or not self.db:
            return

        year_month: str = batch[0]["ReceivedAt"].strftime("%Y%m")
        table_name: str = await self.create_monthly_table(year_month)

        sql_command: str = (
            f"INSERT INTO {table_name} (Facility, Priority, FromHost, InfoUnitID, "
            "ReceivedAt, DeviceReportedTime, SysLogTag, ProcessID, Message) VALUES "
            "(:Facility, :Priority, :FromHost, :InfoUnitID, :ReceivedAt, "
            ":DeviceReportedTime, :SysLogTag, :ProcessID, :Message)"
        )

        if SQL_DUMP:
            print(f"\n   SQL: {sql_command}")
            summary: str = (
                f"PARAMS: {batch[0]} and {len(batch) - 1} more logs..."
                if len(batch) > 1
                else f"PARAMS: {batch[0]}"
            )
            print(f"  {summary}")

        try:
            async with self.db.cursor() as cursor:
                # Execute the insert and FTS sync concurrently
                await asyncio.gather(
                    cursor.executemany(sql_command, batch),
                    self.sync_fts_for_month(year_month),
                )
            await self.db.commit()
            if DEBUG:
                print(f"Successfully wrote batch of {len(batch)} messages.")
        except Exception as e:
            if DEBUG and not self._shutting_down:
                print(f"\nBATCH SQL_ERROR: {e}")
                await self.db.rollback()

    async def connect_to_sqlite(self) -> None:
        """Initializes the database connection."""
        self.db = await aiosqlite.connect(SQLITE_DB_PATH)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA auto_vacuum = FULL")
        await self.db.commit()
        print(f"SQLite database '{SQLITE_DB_PATH}' connected.")

    async def create_monthly_table(self, year_month: str) -> str:
        """Creates tables for the given month if they don't exist."""
        table_name: str = f"SystemEvents{year_month}"
        fts_table_name: str = f"SystemEventsFTS{year_month}"
        if not self.db:
            raise ConnectionError("Database is not connected.")

        async with self.db.cursor() as cursor:
            await cursor.execute(
                "SELECT name FROM sqlite_master "
                f"WHERE type='table' AND name='{table_name}'"
            )
            if await cursor.fetchone() is None:
                if DEBUG:
                    print(
                        "Creating new tables for "
                        f"{year_month}: {table_name}, {fts_table_name}"
                    )
                await self.db.execute(
                    f"""CREATE TABLE {table_name} (
                    ID INTEGER PRIMARY KEY AUTOINCREMENT, Facility INTEGER,
                    Priority INTEGER, FromHost TEXT, InfoUnitID INTEGER,
                    ReceivedAt TIMESTAMP, DeviceReportedTime TIMESTAMP,
                    SysLogTag TEXT, ProcessID TEXT, Message TEXT
                )"""
                )
                await self.db.execute(
                    f"CREATE INDEX idx_ReceivedAt_{year_month} "
                    f"ON {table_name} (ReceivedAt)"
                )
                await self.db.execute(
                    f"CREATE VIRTUAL TABLE {fts_table_name} "
                    f"USING fts5(Message, content='{table_name}', content_rowid='ID')"
                )
                await self.db.commit()
        return table_name

    async def sync_fts_for_month(self, year_month: str) -> None:
        """Syncs the FTS index for a given month."""
        if (
            not SQL_WRITE
            or (self._shutting_down and not self.db)
            or not self.db
        ):
            return

        fts_table_name: str = f"SystemEventsFTS{year_month}"
        try:
            async with self.db.cursor() as cursor:
                await cursor.execute(
                    f"INSERT INTO {fts_table_name}({fts_table_name}) VALUES('rebuild')"
                )
            await self.db.commit()
            if DEBUG:
                print(f"Synced FTS table {fts_table_name}.")
        except Exception as e:
            if DEBUG and not self._shutting_down:
                print(f"Failed to sync FTS table {fts_table_name}: {e}")

    async def shutdown(self) -> None:
        """Gracefully shuts down the server."""
        print("\nShutting down server...")
        self._shutting_down = True

        if self.transport:
            self.transport.close()

        if self._db_writer_task:
            print("Waiting for database writer to finish...")
            # Give the writer a moment to process the last items
            await asyncio.sleep(0.1)
            # The writer task loop will exit gracefully.
            await self._db_writer_task

        if self.db:  # Close the database connection if it exists
            await self.db.close()
            print("Database connection closed.")


class AsyncQueueIterator:
    """Asynchronous iterator for asyncio.Queue."""

    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Tuple[bytes, Tuple[str, int], datetime]:
        try:
            # Use a short timeout to allow checking shutdown conditions
            return await asyncio.wait_for(
                self.queue.get(), timeout=BATCH_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise StopAsyncIteration


async def run_server() -> None:
    """Sets up and runs the server until a shutdown signal is received."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    # Use the async factory to create the server instance
    server: SyslogUDPServer = await SyslogUDPServer.create(
        host=BINDING_IP, port=BINDING_PORT
    )

    # Setup signal handlers
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    transport, _ = await loop.create_datagram_endpoint(
        lambda: server, local_addr=(server.host, server.port)
    )
    print(f"Server is running. Press Ctrl+C to stop.")

    try:
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
