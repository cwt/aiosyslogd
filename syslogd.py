#!/usr/bin/env python
# -*- coding: utf-8 -*-
## Syslog Server in Python with asyncio and SQLite.

# Configuration
import os

DEBUG = os.environ.get("DEBUG") == "True"
LOG_DUMP = os.environ.get("LOG_DUMP") == "True"
SQL_DUMP = os.environ.get("SQL_DUMP") == "True"
SQL_WRITE = os.environ.get("SQL_WRITE") == "True"
BINDING_IP = os.environ.get("BINDING_IP", "0.0.0.0")
BINDING_PORT = os.environ.get("BINDING_PORT", "5140")
del os

# Code
import asyncio
import aiosqlite
import signal
from datetime import datetime
from priority import SyslogMatrix
from rfc5424 import convert_rfc5424_to_rfc3164

try:
    import uvloop
except ImportError:
    uvloop = None


class SyslogUDPServer:

    syslog_matrix = SyslogMatrix()

    def __init__(self, host, port, loop=None):
        self.host = host
        self.port = port
        self.loop = loop or asyncio.get_event_loop()
        self.running = False

        # Register the signal handler for SIGINT (Control-C)
        self.loop.add_signal_handler(signal.SIGINT, self.handle_sigint_wrapper)

        self.setup()
        print(f"Listening on UDP {host}:{port} using {self.loop.__module__}")

    def setup(self):
        if SQL_WRITE:
            self.loop.run_until_complete(self.connect_to_sqlite())

    async def handle_sigint(self, signum, frame):
        print("\nSIGINT received. Shutting down gracefully...")
        self.running = False
        await self.shutdown()

    def handle_sigint_wrapper(self):
        asyncio.ensure_future(self.handle_sigint(signal.SIGINT, None))

    async def close_db_connection(self):
        if SQL_WRITE and hasattr(self, "db") and self.db:
            try:
                await self.db.close()
            except Exception as e:
                if DEBUG:
                    print(f"Error while closing the database connection: {e}")

    async def shutdown(self):
        self.stop()
        await self.close_db_connection()
        self.loop.stop()

    async def connect_to_sqlite(self):
        self.db = await aiosqlite.connect("syslog.db", loop=self.loop)
        # Enable WAL mode for better write performance
        await self.db.execute("PRAGMA journal_mode=WAL")
        # Enable auto_vacuum to keep the database size in check
        await self.db.execute("PRAGMA auto_vacuum = FULL")
        await self.db.commit()

    async def create_monthly_table(self, year_month):
        table_name = f"SystemEvents{year_month}"
        fts_table_name = f"SystemEventsFTS{year_month}"
        await self.db.execute(
            f"""CREATE TABLE IF NOT EXISTS {table_name} (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Facility INTEGER,
            Priority INTEGER,
            FromHost TEXT,
            InfoUnitID INTEGER,
            ReceivedAt TIMESTAMP,
            DeviceReportedTime TIMESTAMP,
            SysLogTag TEXT,
            ProcessID TEXT,
            Message TEXT
        )"""
        )
        await self.db.execute(
            f"""CREATE INDEX IF NOT EXISTS idx_ReceivedAt_{year_month} 
            ON {table_name} (ReceivedAt)"""
        )
        await self.db.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS {fts_table_name}
            USING FTS5(Message)"""
        )
        await self.db.commit()
        return table_name

    def escape(self, msg):
        """
        Escapes special characters in the message to prevent SQL injection.
        Handles strings safely for SQLite insertion.
        """
        if not isinstance(msg, str):
            return str(msg)
        escaped_msg = (
            msg.replace("'", "''")  # Escapes single quotes for SQLite
            .replace('"', '""')  # Escapes double quotes
            .replace("\\", "\\\\")  # Escapes backslashes
        )
        return escaped_msg

    async def handle_datagram(self, data, address):
        """
        Handle RFC-3164 message
        e.g. Jun 21 10:54:52 Main-LB forward: in:LAN out:eth11-WAN-TOT
             [m][d] [---t--] [hostn-program]  [--------message-------]
        """
        ReceivedAt = datetime.now()
        data = data.decode()
        data = convert_rfc5424_to_rfc3164(data)
        if LOG_DUMP:
            print(f"\n  DATA: {data}")

        # Get current year and month for table name
        year_month = ReceivedAt.strftime("%Y%m")  # e.g., "202503"
        table_name = await self.create_monthly_table(year_month)

        datetime_hostname_program = data[data.find(">") + 1 : data.find(": ")]
        m, d, t, hostname = datetime_hostname_program.split()[:4]
        formatted_datetime = f"{m} {d.zfill(2)} {t} {ReceivedAt.year}"
        DeviceReportedTime = datetime.strptime(formatted_datetime, "%b %d %H:%M:%S %Y")
        time_delta = ReceivedAt - DeviceReportedTime
        if abs(time_delta.days) > 1:
            formatted_datetime = f"{m} {d.zfill(2)} {t} {ReceivedAt.year - 1}"
            DeviceReportedTime = datetime.strptime(
                formatted_datetime, "%b %d %H:%M:%S %Y"
            )
        time_delta = ReceivedAt - DeviceReportedTime
        if abs(time_delta.days) > 1:
            pass  # Something wrong, just ignore it.
        else:
            program = "-".join(datetime_hostname_program.split()[4:])
            if "[" in program:
                SysLogTag = program[: program.find("[")]
            else:
                SysLogTag = program
            if "[" in program:
                ProcessID = program[program.find("[") + 1 : program.find("]")]
            else:
                ProcessID = "0"
            Message = data[data.find(": ") + 2 :]
            code = data[data.find("<") + 1 : data.find(">")]
            Facility, Priority = self.syslog_matrix.decode_int(code)
            FromHost = hostname or address[0]
            InfoUnitID = 1

            FromHost = self.escape(FromHost)
            SysLogTag = self.escape(SysLogTag)
            ProcessID = self.escape(ProcessID)
            Message = self.escape(Message)

            params = {
                "Facility": Facility,
                "Priority": Priority,
                "FromHost": FromHost,
                "InfoUnitID": InfoUnitID,
                "ReceivedAt": ReceivedAt.strftime("%Y-%m-%d %H:%M:%S"),
                "DeviceReportedTime": DeviceReportedTime.strftime("%Y-%m-%d %H:%M:%S"),
                "SysLogTag": SysLogTag,
                "ProcessID": ProcessID,
                "Message": Message,
            }

            sql_command = (
                f"INSERT INTO {table_name} (Facility, Priority, FromHost, "
                "InfoUnitID, ReceivedAt, DeviceReportedTime, SysLogTag, "
                "ProcessID, Message) VALUES "
                "(:Facility, :Priority, :FromHost, :InfoUnitID, :ReceivedAt, "
                ":DeviceReportedTime, :SysLogTag, :ProcessID, :Message)"
            )

            if SQL_DUMP:
                print(f"\n   SQL: {sql_command}")
                print(f"\nPARAMS: {params}")

            if SQL_WRITE:
                try:
                    async with self.db.execute(sql_command, params) as cursor:
                        pass
                    # Optionally sync to FTS table if needed
                    await self.db.execute(
                        f"INSERT INTO SystemEventsFTS{year_month} (Message) VALUES (?)",
                        (Message,),
                    )
                    await self.db.commit()
                except Exception as e:
                    if DEBUG:
                        print(f"\n   SQL: {sql_command}")
                        print(f"\nPARAMS: {params}")
                        print(f"\nEXCEPT: {e}")
                    await self.db.rollback()

    def start(self):
        self.endpoint, _ = self.loop.run_until_complete(
            self.loop.create_datagram_endpoint(
                lambda: DatagramProtocol(self.handle_datagram),
                local_addr=(self.host, self.port),
            )
        )

    def stop(self):
        self.endpoint.close()


class DatagramProtocol:
    def __init__(self, datagram_callback):
        self.datagram_callback = datagram_callback

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        asyncio.create_task(self.datagram_callback(data, addr))

    def error_received(self, exc):
        if DEBUG:
            print(f"Error received: {exc}")

    def connection_lost(self, exc):
        if DEBUG:
            print("Closing transport")


if __name__ == "__main__":
    loop = uvloop.new_event_loop() if uvloop else None
    try:
        syslog_server = SyslogUDPServer(BINDING_IP, BINDING_PORT, loop)
        syslog_server.start()
        loop = syslog_server.loop
        loop.run_forever()
    except (IOError, SystemExit):
        raise
    except Exception as e:
        print(f"Error occurred: {e}")
    finally:
        print("Shutting down the server...")
        syslog_server.stop()
        loop.run_until_complete(syslog_server.close_db_connection())
        loop.close()
