#!/usr/bin/env python
# -*- coding: utf-8 -*-
## Syslog Server in Python with asyncio and SQLite.

import asyncio
import aiosqlite
import signal
import os
import re
from datetime import datetime, UTC
from typing import Dict, Any, Tuple, List, Type, Self
from types import ModuleType

# Relative imports for package structure
from .priority import SyslogMatrix

uvloop: ModuleType | None = None
try:
    import uvloop

except ImportError:
    pass


# --- Configuration ---
# Environment variables are now read inside the functions that use them.
DEBUG: bool = os.environ.get("DEBUG") == "True"
LOG_DUMP: bool = os.environ.get("LOG_DUMP") == "True"
SQL_DUMP: bool = os.environ.get("SQL_DUMP") == "True"
SQL_WRITE: bool = os.environ.get("SQL_WRITE") == "True"
BINDING_IP: str = os.environ.get("BINDING_IP", "0.0.0.0")
BINDING_PORT: int = int(os.environ.get("BINDING_PORT", "5140"))
BATCH_SIZE: int = int(os.environ.get("BATCH_SIZE", "1000"))
BATCH_TIMEOUT: int = int(os.environ.get("BATCH_TIMEOUT", "5"))


# --- Conversion Utilities ---


def convert_rfc3164_to_rfc5424(message: str, debug_mode: bool = False) -> str:
    """
    Converts a best-effort RFC 3164 syslog message to an RFC 5424 message.
    This version is more flexible to handle formats like FortiGate's.
    """
    # Pattern for RFC 3164: <PRI>MMM DD HH:MM:SS HOSTNAME TAG[PID]: MSG
    # Made the colon after the tag optional and adjusted tag capture.
    pattern: re.Pattern[str] = re.compile(
        r"<(?P<pri>\d{1,3})>"
        r"(?P<mon>\w{3})\s+(?P<day>\d{1,2})\s+(?P<hr>\d{2}):(?P<min>\d{2}):(?P<sec>\d{2})"
        r"\s+(?P<host>[\w\-\.]+)"
        r"\s+(?P<tag>\S+?)(:|\s-)?\s"  # Flexible tag/separator matching
        r"(?P<msg>.*)",
        re.DOTALL,
    )
    match: re.Match[str] | None = pattern.match(message)

    if not match:
        if debug_mode:
            print(
                f"[RFC-CONVERT] Not an RFC 3164 message, returning original: {message}"
            )
        return message

    parts: Dict[str, str] = match.groupdict()
    priority: str = parts["pri"]
    hostname: str = parts["host"]
    raw_tag: str = parts["tag"]
    msg: str = parts["msg"].strip()

    app_name: str = raw_tag
    procid: str = "-"
    pid_match: re.Match[str] | None = re.match(r"^(.*)\[(\d+)\]$", raw_tag)
    if pid_match:
        app_name = pid_match.group(1)
        procid = pid_match.group(2)

    try:
        now: datetime = datetime.now()
        dt_naive: datetime = datetime.strptime(
            f"{parts['mon']} {parts['day']} {parts['hr']}:{parts['min']}:{parts['sec']}",
            "%b %d %H:%M:%S",
        ).replace(year=now.year)

        if dt_naive > now:
            dt_naive = dt_naive.replace(year=now.year - 1)

        dt_aware: datetime = dt_naive.astimezone().astimezone(UTC)
        timestamp: str = dt_aware.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except ValueError:
        if debug_mode:
            print(
                "[RFC-CONVERT] Could not parse RFC-3164 timestamp, using current time."
            )
        timestamp = (
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )

    return f"<{priority}>1 {timestamp} {hostname} {app_name} {procid} - - {msg}"


def normalize_to_rfc5424(message: str, debug_mode: bool = False) -> str:
    """
    Ensures a syslog message is in RFC 5424 format.
    Converts RFC 3164 messages, and leaves RFC 5424 as is.
    """
    pri_end: int = message.find(">")
    if pri_end > 0 and len(message) > pri_end + 2:
        if message[pri_end + 1] == "1" and message[pri_end + 2].isspace():
            return message

    return convert_rfc3164_to_rfc5424(message, debug_mode)


class SyslogUDPServer(asyncio.DatagramProtocol):
    """An asynchronous Syslog UDP server with batch database writing."""

    syslog_matrix: SyslogMatrix = SyslogMatrix()
    RFC5424_PATTERN: re.Pattern[str] = re.compile(
        r"<(?P<pri>\d+)>"
        r"(?P<ver>\d+)\s"
        r"(?P<ts>\S+)\s"
        r"(?P<host>\S+)\s"
        r"(?P<app>\S+)\s"
        r"(?P<pid>\S+)\s"
        r"(?P<msgid>\S+)\s"
        r"(?P<sd>(\-|(?:\[.+?\])+))\s?"
        r"(?P<msg>.*)",
        re.DOTALL,
    )

    # __init__ is now synchronous and lightweight
    def __init__(self, host: str, port: int) -> None:
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

    # An async factory to properly create and initialize the server
    @classmethod
    async def create(cls: Type[Self], host: str, port: int) -> Self:
        server = cls(host, port)
        print(f"aiosyslogd starting on UDP {host}:{port}...")
        if SQL_WRITE:
            print(
                f"SQLite writing ENABLED. Batch size: {BATCH_SIZE}, Timeout: {BATCH_TIMEOUT}s"
            )
            await server.connect_to_sqlite()
        if DEBUG:
            print("Debug mode is ON.")
        return server

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
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
        if DEBUG:
            print(f"Error received: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        if DEBUG:
            print(f"Connection lost: {exc}")

    async def database_writer(self) -> None:
        """A dedicated task to write messages to the database in batches."""
        batch: List[Dict[str, Any]] = []
        while not self._shutting_down or not self._message_queue.empty():
            try:
                data, addr, received_at = await asyncio.wait_for(
                    self._message_queue.get(), timeout=BATCH_TIMEOUT
                )
                params = self.process_datagram(data, addr, received_at)
                if params:
                    batch.append(params)
                self._message_queue.task_done()
                if len(batch) >= BATCH_SIZE:
                    await self.write_batch_to_db(batch)
                    batch.clear()
            except asyncio.TimeoutError:
                if batch:
                    await self.write_batch_to_db(batch)
                    batch.clear()
            except Exception as e:
                if DEBUG:
                    print(f"[DB-WRITER-ERROR] {e}")
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
            match: re.Match[str] | None = self.RFC5424_PATTERN.match(
                processed_data
            )
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
            await self.db.executemany(sql_command, batch)
            await self.db.commit()
            await self.sync_fts_for_month(year_month)
            if DEBUG:
                print(f"Successfully wrote batch of {len(batch)} messages.")
        except Exception as e:
            if DEBUG and not self._shutting_down:
                print(f"\nBATCH SQL_ERROR: {e}")
                await self.db.rollback()

    async def connect_to_sqlite(self) -> None:
        """Initializes the database connection."""
        self.db = await aiosqlite.connect("syslog.db")
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA auto_vacuum = FULL")
        await self.db.commit()
        print("SQLite database connected.")

    async def create_monthly_table(self, year_month: str) -> str:
        """Creates tables for the given month if they don't exist."""
        table_name: str = f"SystemEvents{year_month}"
        fts_table_name: str = f"SystemEventsFTS{year_month}"
        if not self.db:
            raise ConnectionError("Database is not connected.")

        async with self.db.cursor() as cursor:
            await cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
            )
            if await cursor.fetchone() is None:
                if DEBUG:
                    print(
                        f"Creating new tables for {year_month}: {table_name}, {fts_table_name}"
                    )
                await self.db.execute(
                    f"""CREATE TABLE {table_name} (
                    ID INTEGER PRIMARY KEY AUTOINCREMENT, Facility INTEGER, Priority INTEGER,
                    FromHost TEXT, InfoUnitID INTEGER, ReceivedAt TIMESTAMP, DeviceReportedTime TIMESTAMP,
                    SysLogTag TEXT, ProcessID TEXT, Message TEXT
                )"""
                )
                await self.db.execute(
                    f"CREATE INDEX idx_ReceivedAt_{year_month} ON {table_name} (ReceivedAt)"
                )
                await self.db.execute(
                    f"CREATE VIRTUAL TABLE {fts_table_name} USING fts5(Message, content='{table_name}', content_rowid='ID')"
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
            await self.db.execute(
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

        if self.db:
            await self.db.close()
            print("Database connection closed.")


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
