#!/usr/bin/env python
# -*- coding: utf-8 -*-
## Syslog Server in Python with asyncio and SQLite.

# Configuration
import os
DEBUG = os.environ.get('DEBUG') == 'True'
LOG_DUMP = os.environ.get('LOG_DUMP') == 'True'
SQL_DUMP = os.environ.get('SQL_DUMP') == 'True'
SQL_WRITE = os.environ.get('SQL_WRITE') == 'True'
BINDING_IP = os.environ.get('BINDING_IP', '0.0.0.0')
BINDING_PORT = os.environ.get('BINDING_PORT', '5140')
del(os)

# Query
SQL = ("INSERT INTO SystemEvents (Facility, Priority, FromHost, "
       "InfoUnitID, ReceivedAt, DeviceReportedTime, SysLogTag, "
       "ProcessID, Message) VALUES "
       "(:Facility, :Priority, :FromHost, :InfoUnitID, :ReceivedAt, "
       ":DeviceReportedTime, :SysLogTag, :ProcessID, :Message)")

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
        print(f'Listening on UDP {host}:{port} using {self.loop.__module__}')

    def setup(self):
        if SQL_WRITE:
            self.loop.run_until_complete(self.connect_to_sqlite())

    async def handle_sigint(self, signum, frame):
        print("\nSIGINT received. Shutting down gracefully...")
        self.running = False

        # Run the shutdown coroutine in the event loop and wait for completion
        await self.shutdown()

    def handle_sigint_wrapper(self):
        # Pass signum and frame arguments to handle_sigint()
        asyncio.ensure_future(self.handle_sigint(signal.SIGINT, None))

    async def close_db_connection(self):
        if SQL_WRITE and hasattr(self, 'db') and self.db:
            try:
                await self.db.close()
            except Exception as e:
                if DEBUG:
                    print("Error while closing the database connection:", e)

    async def shutdown(self):
        # Stop the server
        self.stop()

        # Close the database connection if SQL_WRITE is True
        await self.close_db_connection()

        # Stop the event loop
        self.loop.stop()

    async def connect_to_sqlite(self):
        self.db = await aiosqlite.connect('syslog.db', loop=self.loop)
        # Enable WAL mode for better write performance
        await self.db.execute('PRAGMA journal_mode=WAL')
        await self.db.execute('''CREATE TABLE IF NOT EXISTS SystemEvents (
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
        )''')
        await self.db.commit()

        # Create an index on the ReceivedAt field
        await self.db.execute('''CREATE INDEX IF NOT EXISTS idx_ReceivedAt 
            ON SystemEvents (ReceivedAt)''')
        await self.db.commit()

        # Create the virtual table with the full-text search index
        await self.db.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS SystemEventsFTS
            USING FTS5(Message)''')
        await self.db.commit()

    async def escape(self, msg):
        return msg

    async def handle_datagram(self, data, address):
        ReceivedAt = datetime.now()  # Generated
        data = data.decode()
        data = convert_rfc5424_to_rfc3164(data)
        if LOG_DUMP:
            print(data)
        datetime_hostname_program = data[data.find('>') + 1:data.find(': ')]
        m, d, t = datetime_hostname_program.split()[:3]
        formatted_datetime = '%s %s %s %s' % (
            m, d.zfill(2), t, ReceivedAt.year
        )
        DeviceReportedTime = datetime.strptime(
            formatted_datetime, '%b %d %H:%M:%S %Y'
        )
        time_delta = ReceivedAt - DeviceReportedTime
        if abs(time_delta.days) > 1:
            formatted_datetime = '%s %s %s %s' % (
                m, d.zfill(2), t, ReceivedAt.year - 1
            )
            DeviceReportedTime = datetime.strptime(
                formatted_datetime, '%b %d %H:%M:%S %Y'
            )
        time_delta = ReceivedAt - DeviceReportedTime
        if abs(time_delta.days) > 1:
            pass  # Something wrong, just ignore it.
        else:
            hostname, program = datetime_hostname_program.split()[-2:]
            if '[' in program:
                SysLogTag = program[:program.find('[')]
            else:
                SysLogTag = program
            if '[' in program:
                ProcessID = program[program.find('[') + 1:program.find(']')]
            else:
                ProcessID = '0'
            Message = data[data.find(': ') + 2:]
            code = data[data.find('<') + 1:data.find('>')]
            Facility, Priority = self.syslog_matrix.decode_int(code)
            FromHost = hostname or address[0]
            InfoUnitID = 1  # Hardcoded

            params = {
                'Facility': Facility,
                'Priority': Priority,
                'FromHost': FromHost,
                'InfoUnitID': InfoUnitID,
                'ReceivedAt': ReceivedAt.strftime('%Y-%m-%d %H:%M:%S'),
                'DeviceReportedTime': DeviceReportedTime.strftime('%Y-%m-%d %H:%M:%S'),
                'SysLogTag': SysLogTag,
                'ProcessID': ProcessID,
                'Message': Message
            }

            sql_command = SQL

            if SQL_DUMP:
                print('\nSQL:', sql_command, params)

            if SQL_WRITE:
                try:
                    async with self.db.execute(sql_command, params) as cursor:
                        pass
                except Exception as e:
                    if DEBUG:
                        print(sql_command, params)
                        print(e)
                    await self.db.rollback()
                else:
                    await self.db.commit()

    def start(self):
        self.endpoint, _ = self.loop.run_until_complete(
            self.loop.create_datagram_endpoint(
                lambda: DatagramProtocol(self.handle_datagram),
                local_addr=(self.host, self.port)
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
            print('Error received:', exc)

    def connection_lost(self, exc):
        if DEBUG:
            print('Closing transport')


if __name__ == "__main__":
    loop = uvloop.new_event_loop() if uvloop else None
    try:
        syslog_server = SyslogUDPServer(BINDING_IP, BINDING_PORT, loop)
        syslog_server.start()
        loop = syslog_server.loop

        # Run the event loop
        loop.run_forever()

    except (IOError, SystemExit):
        raise
    except Exception as e:
        print("Error occurred:", e)
    finally:
        print("Shutting down the server...")
        syslog_server.stop()
        loop.run_until_complete(syslog_server.close_db_connection())
        loop.close()

