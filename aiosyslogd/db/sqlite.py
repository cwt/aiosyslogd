# -*- coding: utf-8 -*-
from . import BaseDatabase
from datetime import datetime
from typing import Any, Dict, List
import aiosqlite
import os
from loguru import logger


class SQLiteDriver(BaseDatabase):
    """
    SQLite database driver that creates a new database file for each month.
    Optimized to handle month-boundary batches efficiently.
    """

    def __init__(self, config: Dict[str, Any]):
        self.db_path_template = config.get("database", "syslog.sqlite3")
        self.sql_dump = config.get("sql_dump", False)
        self.debug = config.get("debug", False)
        self.db: aiosqlite.Connection | None = None
        self._current_db_path: str | None = None

    def _get_db_path_for_month(self, dt: datetime) -> str:
        """Generates a monthly database filename, e.g., syslog_202506.sqlite3"""
        base, ext = os.path.splitext(self.db_path_template)
        return f"{base}_{dt.strftime('%Y%m')}{ext}"

    async def connect(self) -> None:
        """Initial connection is handled dynamically on the first write."""
        logger.info(
            "SQLite driver initialized. Connection will be made on first write."
        )
        pass

    async def close(self) -> None:
        """Closes the current database connection if it exists."""
        if self.db:
            await self.db.close()
            logger.debug(
                f"SQLite connection to '{self._current_db_path}' closed."
            )
            self.db = None
            self._current_db_path = None

    async def _switch_db_if_needed(self, dt: datetime) -> None:
        """
        Checks if the database connection needs to be switched to a new month's file.
        If so, it handles the reconnection and initial table setup.
        """
        target_db_path = self._get_db_path_for_month(dt)
        if target_db_path != self._current_db_path:
            if self.db:
                await self.close()

            logger.info(
                f"Month changed. Switching connection to '{target_db_path}'..."
            )
            self.db = await aiosqlite.connect(target_db_path)
            await self.db.execute("PRAGMA journal_mode=WAL;")
            await self.db.commit()
            self._current_db_path = target_db_path
            logger.info(f"Successfully connected to '{target_db_path}'.")
            await self.create_monthly_table("SystemEvents")

    async def create_monthly_table(self, table_name: str) -> None:
        """Creates tables for the given month if they don't exist."""
        fts_table_name = f"{table_name}_FTS"
        if not self.db:
            raise ConnectionError("Database is not connected.")
        async with self.db.cursor() as cursor:
            await cursor.execute(
                "SELECT name FROM sqlite_master "
                f"WHERE type='table' AND name='{table_name}'"
            )
            if await cursor.fetchone() is None:
                logger.debug(
                    f"Creating new tables and indexes in {self._current_db_path}: "
                    f"{table_name}, {fts_table_name}"
                )
                await self.db.execute(
                    f"""CREATE TABLE {table_name} (
                    ID INTEGER PRIMARY KEY AUTOINCREMENT, Facility INTEGER,
                    Priority INTEGER, FromHost TEXT, InfoUnitID INTEGER,
                    ReceivedAt TIMESTAMP, DeviceReportedTime TIMESTAMP,
                    SysLogTag TEXT, ProcessID TEXT, Message TEXT)"""
                )
                await self.db.execute(
                    f"CREATE INDEX idx_{table_name}_ReceivedAt ON {table_name} (ReceivedAt)"
                )
                await self.db.execute(
                    f"CREATE INDEX idx_{table_name}_FromHost ON {table_name} (FromHost)"
                )
                await self.db.execute(
                    f"CREATE VIRTUAL TABLE {fts_table_name} "
                    f"USING fts5(Message, content='{table_name}', content_rowid='ID')"
                )
                await self.db.execute(
                    f"""CREATE TRIGGER {table_name}_insert AFTER INSERT ON {table_name}
                    BEGIN
                        INSERT INTO {fts_table_name}(rowid, Message)
                        VALUES (new.ID, new.Message);
                    END"""
                )
                await self.db.execute(
                    f"""CREATE TRIGGER {table_name}_update AFTER UPDATE ON {table_name}
                    BEGIN
                        UPDATE {fts_table_name}
                        SET Message = new.Message
                        WHERE rowid = new.ID;
                    END"""
                )
                await self.db.execute(
                    f"""CREATE TRIGGER {table_name}_delete AFTER DELETE ON {table_name}
                    BEGIN
                        DELETE FROM {fts_table_name}
                        WHERE rowid = old.ID;
                    END"""
                )
                await self.db.commit()

    # NEW: Private helper method to handle writing a homogenous (single-month) batch.
    async def _write_sub_batch(self, sub_batch: List[Dict[str, Any]]):
        """Writes a sub-batch of logs that all belong to the same month."""
        try:
            await self._switch_db_if_needed(sub_batch[0]["ReceivedAt"])
            if not self.db:
                logger.error("DB connection failed. Skipping sub-batch.")
                return

            table_name = "SystemEvents"
            sql_command = (
                f"INSERT INTO {table_name} (Facility, Priority, FromHost, InfoUnitID, "
                "ReceivedAt, DeviceReportedTime, SysLogTag, ProcessID, Message) "
                "VALUES (:Facility, :Priority, :FromHost, :InfoUnitID, :ReceivedAt, "
                ":DeviceReportedTime, :SysLogTag, :ProcessID, :Message)"
            )
            await self.db.executemany(sql_command, sub_batch)
            await self.db.commit()
            if self.sql_dump:
                logger.trace(f"SQL: {sql_command}")
                logger.trace(f"PARAMS: {sub_batch[0]}")
                if len(sub_batch) > 1:
                    logger.trace(f"(...and {len(sub_batch) - 1} more logs...)")

            logger.debug(
                f"Successfully wrote {len(sub_batch)} logs to '{self._current_db_path}'."
            )
        except Exception as e:
            logger.opt(exception=True).error("Batch SQL write failed")
            logger.debug(str(e))
            if self.db:
                await self.db.rollback()

    # UPDATED: The optimized write_batch method with a fast path.
    async def write_batch(self, batch: List[Dict[str, Any]]) -> None:
        """
        Efficiently writes a batch of logs, using a fast path for most cases
        and a partitioning path only for batches that span a month boundary.
        """
        if not batch:
            return

        first_msg_dt = batch[0]["ReceivedAt"]
        last_msg_dt = batch[-1]["ReceivedAt"]

        # Check if the batch might span a month boundary. This is a very fast check.
        if (
            first_msg_dt.month == last_msg_dt.month
            and first_msg_dt.year == last_msg_dt.year
        ):
            # --- FAST PATH (99.99% of cases) ---
            # The whole batch is in the same month, write it directly.
            await self._write_sub_batch(batch)
        else:
            # --- SLOW PATH (Rare month-boundary case) ---
            # Partition the batch by month and write each sub-batch.
            logger.debug("Month boundary detected in batch, partitioning...")
            batches_by_month: Dict[str, List[Dict[str, Any]]] = {}
            for msg in batch:
                month_key = msg["ReceivedAt"].strftime("%Y%m")
                if month_key not in batches_by_month:
                    batches_by_month[month_key] = []
                batches_by_month[month_key].append(msg)

            for month_batch in batches_by_month.values():
                await self._write_sub_batch(month_batch)


SqliteDriver = SQLiteDriver  # For driver loader with {DB_DRIVER.capitalize()}
