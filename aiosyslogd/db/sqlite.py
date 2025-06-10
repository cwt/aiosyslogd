# -*- coding: utf-8 -*-
import aiosqlite
from typing import Any, Dict, List
from . import BaseDatabase


class SQLiteDriver(BaseDatabase):
    """SQLite database driver."""

    def __init__(self, config: Dict[str, Any]):
        self.db_path = config.get("database", "syslog.sqlite3")
        self.sql_dump = config.get("sql_dump", False)
        self.debug = config.get("debug", False)
        self.db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Initializes the database connection."""
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA auto_vacuum = FULL")
        await self.db.commit()
        print(f"SQLite database '{self.db_path}' connected.")

    async def close(self) -> None:
        """Closes the database connection."""
        if self.db:
            await self.db.close()
            print("SQLite connection closed.")

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
                if self.debug:
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

    async def write_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Writes a batch of messages to the database."""
        if not batch or not self.db:
            return

        year_month: str = batch[0]["ReceivedAt"].strftime("%Y%m")
        table_name: str = await self.create_monthly_table(year_month)

        sql_command: str = (
            f"INSERT INTO {table_name} (Facility, Priority, FromHost, InfoUnitID, "
            "ReceivedAt, DeviceReportedTime, SysLogTag, ProcessID, Message) VALUES "
            "(:Facility, :Priority, :FromHost, :InfoUnitID, :ReceivedAt, "
            ":DeviceReportedTime, :SysLogTag, :ProcessID, :Message)"
        )

        if self.sql_dump:
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
            if self.debug:
                print(
                    f"Successfully wrote batch of {len(batch)} messages to SQLite."
                )
        except Exception as e:
            if self.debug:
                print(f"\nBATCH SQL_ERROR (SQLite): {e}")
            await self.db.rollback()


SqliteDriver = SQLiteDriver  # For driver loader with {DB_DRIVER.capitalize()}
