from dataclasses import dataclass
from datetime import datetime, timedelta
from loguru import logger as _logger
from typing import Any, Dict, List, Tuple
import aiosqlite
import asyncio
import glob
import os
import sqlite3
import sys
import time


# --- Helper Functions ---
async def get_available_databases(cfg: Dict) -> List[str]:
    """Finds available monthly SQLite database files."""
    db_template: str = (
        cfg.get("database", {})
        .get("sqlite", {})
        .get("database", "syslog.sqlite3")
    )
    base, ext = os.path.splitext(db_template)
    search_pattern: str = f"{base}_*{ext}"
    # In case that there are a lot of files,
    # we use asyncio.to_thread to avoid blocking the event loop.
    files: List[str] = await asyncio.to_thread(glob.glob, search_pattern)
    files.sort(reverse=True)
    return files


async def get_time_boundary_ids(
    conn: aiosqlite.Connection, min_time_filter: str, max_time_filter: str
) -> Tuple[int | None, int | None, List[str]]:
    """Determines the start and end IDs based on time filters."""
    start_id: int | None = None
    end_id: int | None = None
    debug_queries: List[str] = []
    db_time_format = "%Y-%m-%d %H:%M:%S"
    chunk_sizes_minutes = [5, 15, 30, 60]

    def _parse_time_string(time_str: str) -> datetime:
        time_str = time_str.replace("T", " ")
        try:
            return datetime.strptime(time_str, db_time_format)
        except ValueError:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M")

    if min_time_filter:
        start_debug_chunks = []
        total_start_time_ms = 0.0
        current_start_dt = _parse_time_string(min_time_filter)
        final_end_dt = (
            _parse_time_string(max_time_filter)
            if max_time_filter
            else datetime.now()
        )
        chunk_index = 0
        while start_id is None and current_start_dt < final_end_dt:
            minutes_to_add = chunk_sizes_minutes[
                min(chunk_index, len(chunk_sizes_minutes) - 1)
            ]
            chunk_end_dt = current_start_dt + timedelta(minutes=minutes_to_add)
            start_sql = (
                "SELECT ID FROM SystemEvents "
                "WHERE ReceivedAt >= ? AND ReceivedAt < ? "
                "ORDER BY ID ASC LIMIT 1"
            )
            start_params = (
                current_start_dt.strftime(db_time_format),
                chunk_end_dt.strftime(db_time_format),
            )
            start_time = time.perf_counter()
            async with conn.execute(start_sql, start_params) as cursor:
                row = await cursor.fetchone()
                start_id = row["ID"] if row else None
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            total_start_time_ms += elapsed_ms
            start_debug_chunks.append(
                f"  - Chunk ({minutes_to_add}m): {start_params} -> "
                f"Found: {start_id is not None} ({elapsed_ms:.2f}ms)"
            )
            current_start_dt = chunk_end_dt
            chunk_index += 1
        debug_queries.append(
            "Boundary Query (Start):\n"
            f"  Result ID: {start_id}\n"
            f"  Total Time: {total_start_time_ms:.2f}ms\n"
            + "\n".join(start_debug_chunks)
        )

    if max_time_filter:
        end_debug_chunks = []
        total_end_time_ms = 0.0
        end_dt = _parse_time_string(max_time_filter)
        next_id_after_end = None
        current_search_dt = end_dt
        total_search_duration = timedelta(0)
        max_search_forward = timedelta(days=1)
        chunk_index = 0
        while (
            next_id_after_end is None
            and total_search_duration < max_search_forward
        ):
            minutes_to_add = chunk_sizes_minutes[
                min(chunk_index, len(chunk_sizes_minutes) - 1)
            ]
            chunk_duration = timedelta(minutes=minutes_to_add)
            chunk_end_dt = current_search_dt + chunk_duration
            end_boundary_sql = (
                "SELECT ID FROM SystemEvents "
                "WHERE ReceivedAt > ? AND ReceivedAt < ? "
                "ORDER BY ID ASC LIMIT 1"
            )
            end_params = (
                current_search_dt.strftime(db_time_format),
                chunk_end_dt.strftime(db_time_format),
            )
            start_time = time.perf_counter()
            async with conn.execute(end_boundary_sql, end_params) as cursor:
                row = await cursor.fetchone()
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            total_end_time_ms += elapsed_ms
            end_debug_chunks.append(
                f"  - Chunk ({minutes_to_add}m): {end_params} -> "
                f"Found: {row is not None} ({elapsed_ms:.2f}ms)"
            )
            if row:
                next_id_after_end = row["ID"]
                break
            current_search_dt = chunk_end_dt
            total_search_duration += chunk_duration
            chunk_index += 1
        if next_id_after_end:
            end_id = next_id_after_end - 1
        else:
            fallback_clauses = ["ReceivedAt <= ?"]
            fallback_params: List[Any] = [end_dt.strftime(db_time_format)]
            if min_time_filter:
                min_dt = _parse_time_string(min_time_filter)
                fallback_clauses.append("ReceivedAt >= ?")
                fallback_params.append(min_dt.strftime(db_time_format))
            fallback_sql = (
                "SELECT MAX(ID) FROM SystemEvents "
                f"WHERE {' AND '.join(fallback_clauses)}"
            )
            start_time = time.perf_counter()
            async with conn.execute(
                fallback_sql, tuple(fallback_params)
            ) as cursor:
                row = await cursor.fetchone()
                end_id = row[0] if row else None
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            total_end_time_ms += elapsed_ms
            end_debug_chunks.append(
                f"  - Fallback MAX(ID) Query -> "
                f"Found: {end_id is not None} ({elapsed_ms:.2f}ms)"
            )
        debug_queries.append(
            "Boundary Query (End):\n"
            f"  Calculated End ID: {end_id}\n"
            f"  Total Time: {total_end_time_ms:.2f}ms\n"
            + "\n".join(end_debug_chunks)
        )

    if max_time_filter and not min_time_filter:
        start_id = 1
    return start_id, end_id, debug_queries


def build_log_query(
    search_query: str,
    filters: Dict[str, str],
    last_id: int | None,
    page_size: int,
    direction: str,
    start_id: int | None,
    end_id: int | None,
) -> Dict[str, Any]:
    """Constructs the SQL query and parameters for fetching logs."""
    main_params: List[Any] = []
    where_clauses: List[str] = []

    # --- 1. Build WHERE clauses based on filters ---

    # ID range filter
    if start_id is not None:
        where_clauses.append("ID >= ?")
        main_params.append(start_id)
    if end_id is not None:
        where_clauses.append("ID <= ?")
        main_params.append(end_id)

    # FromHost filter
    if filters.get("from_host"):
        where_clauses.append("FromHost = ?")
        main_params.append(filters["from_host"])

    # Full-Text Search (FTS) filter
    if search_query:
        fts_subquery_clauses: List[str] = ["Message MATCH ?"]
        fts_subquery_params: List[Any] = [search_query]

        if start_id is not None:
            fts_subquery_clauses.append("rowid >= ?")
            fts_subquery_params.append(start_id)
        if end_id is not None:
            fts_subquery_clauses.append("rowid <= ?")
            fts_subquery_params.append(end_id)

        fts_subquery = (
            "SELECT rowid FROM SystemEvents_FTS "
            f"WHERE {' AND '.join(fts_subquery_clauses)}"
        )
        where_clauses.append(f"ID IN ({fts_subquery})")
        main_params.extend(fts_subquery_params)

    # --- 2. Assemble the base and count queries ---

    from_clause = "FROM SystemEvents"
    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    count_sql = f"SELECT COUNT(*) {from_clause}{where_sql}"
    count_params = list(main_params)

    # --- 3. Assemble the main query with pagination ---

    base_sql = (
        f"SELECT ID, FromHost, ReceivedAt, Message {from_clause}{where_sql}"
    )

    # Add pagination logic
    order_by, id_comparison = (
        ("ASC", ">") if direction == "prev" else ("DESC", "<")
    )
    if last_id is not None:
        pagination_clause = (
            f" {'AND' if where_clauses else 'WHERE'} ID {id_comparison} ?"
        )
        base_sql += pagination_clause
        main_params.append(last_id)

    main_sql = f"{base_sql} ORDER BY ID {order_by} LIMIT {page_size + 1}"

    # --- 4. Return all query parts ---

    return {
        "main_sql": main_sql,
        "main_params": main_params,
        "count_sql": count_sql,
        "count_params": count_params,
        "debug_query": (
            "Main Query:\n"
            f"  Query: {main_sql}\n"
            f"  Parameters: {main_params}"
        ),
    }


# --- Data Structures ---
@dataclass
class QueryContext:
    """A container for all parameters related to a log query."""

    db_path: str
    search_query: str
    filters: Dict[str, Any]
    last_id: int | None
    direction: str
    page_size: int


# --- Core Logic in a Dedicated Class ---
class LogQuery:
    """Handles the logic for fetching and paginating logs from the database."""

    def __init__(self, context: QueryContext, logger: Any = None):
        """Initializes the LogQuery with a QueryContext."""
        if logger is None:  # Use a default logger if none provided
            _logger.remove()
            _logger.add(
                sys.stderr,
                format="[{time:YYYY-MM-DD HH:mm:ss ZZ}] [{process}] [{level}] {message}",
                level="INFO",
            )
        self.logger = logger or _logger
        self.ctx = context
        self.conn: aiosqlite.Connection | None = None
        self.results: Dict[str, Any] = {
            "logs": [],
            "total_logs": 0,
            "page_info": {},
            "debug_info": [],
            "error": None,
        }
        self.start_id: int | None = None
        self.end_id: int | None = None
        # Ensure the expression evaluates to a proper boolean
        self.use_approximate_count = (
            not self.ctx.search_query
            and not self.ctx.filters.get("from_host")
            and bool(
                self.ctx.filters.get("received_at_min")
                or self.ctx.filters.get("received_at_max")
            )
        )

    async def run(self) -> Dict[str, Any]:
        """Executes the full query process and returns the results."""
        try:
            db_uri: str = f"file:{self.ctx.db_path}?mode=ro"
            async with aiosqlite.connect(
                db_uri,
                uri=True,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            ) as conn:
                self.conn = conn
                self.conn.row_factory = aiosqlite.Row

                await self._determine_query_boundaries()

                # If a time filter was applied but resulted in no valid ID range,
                # it means there are no logs in that period. We can stop early.
                time_filter_was_active = bool(
                    self.ctx.filters.get("received_at_min")
                    or self.ctx.filters.get("received_at_max")
                )
                if (
                    time_filter_was_active
                    and self.start_id is None
                    and self.end_id is None
                ):
                    self.logger.debug(
                        "Time filter yielded no results. Short-circuiting query."
                    )
                    return self.results

                await self._get_total_log_count()
                await self._fetch_log_page()
                self._prepare_pagination()

        except (aiosqlite.OperationalError, aiosqlite.DatabaseError) as e:
            self.results["error"] = str(e)
            self.logger.opt(exception=True).error(
                f"Database query failed for {self.ctx.db_path}"
            )

        return self.results

    async def _determine_query_boundaries(self):
        """Calculates start_id and end_id based on time filters."""
        if not self.conn:
            return
        min_filter = self.ctx.filters.get("received_at_min")
        max_filter = self.ctx.filters.get("received_at_max")
        if min_filter or max_filter:
            self.start_id, self.end_id, boundary_queries = (
                await get_time_boundary_ids(
                    self.conn, min_filter or "", max_filter or ""
                )
            )
            self.results["debug_info"].extend(boundary_queries)

    async def _get_total_log_count(self):
        """Gets the total log count, using an approximation if applicable."""
        if not self.conn:
            return

        if self.use_approximate_count and self.end_id is not None:
            self.logger.debug("Using optimized approximate count.")
            start_id_for_count = (
                self.start_id if self.start_id is not None else 1
            )
            self.results["total_logs"] = (self.end_id - start_id_for_count) + 1
        else:
            self.logger.debug("Using standard COUNT(*) query.")
            count_query_parts = build_log_query(
                self.ctx.search_query,
                self.ctx.filters,
                None,
                0,
                "next",
                self.start_id,
                self.end_id,
            )
            async with self.conn.execute(
                count_query_parts["count_sql"],
                count_query_parts["count_params"],
            ) as cursor:
                count_result = await cursor.fetchone()
                if count_result:
                    self.results["total_logs"] = count_result[0]

    async def _fetch_log_page(self):
        """Fetches the actual rows for the current page."""
        if not self.conn:
            return

        effective_start_id = self.start_id
        if (
            self.use_approximate_count
            and self.ctx.last_id is None
            and self.end_id is not None
        ):
            effective_start_id = max(
                self.start_id or 1, self.end_id - self.ctx.page_size - 50
            )
            self.results["debug_info"].append(
                f"Applied fast-path adjustment to start_id: {effective_start_id}"
            )

        query_parts = build_log_query(
            self.ctx.search_query,
            self.ctx.filters,
            self.ctx.last_id,
            self.ctx.page_size,
            self.ctx.direction,
            effective_start_id,
            self.end_id,
        )
        self.results["debug_info"].append(query_parts["debug_query"])

        async with self.conn.execute(
            query_parts["main_sql"], query_parts["main_params"]
        ) as cursor:
            self.results["logs"] = await cursor.fetchall()

    def _prepare_pagination(self):
        """Calculates pagination details based on the fetched logs."""
        if self.ctx.direction == "prev":
            self.results["logs"].reverse()

        has_more = len(self.results["logs"]) > self.ctx.page_size
        self.results["logs"] = self.results["logs"][: self.ctx.page_size]

        page_info = {
            "has_next_page": False,
            "next_last_id": (
                self.results["logs"][-1]["ID"] if self.results["logs"] else None
            ),
            "has_prev_page": False,
            "prev_last_id": (
                self.results["logs"][0]["ID"] if self.results["logs"] else None
            ),
        }

        if self.ctx.direction == "prev":
            page_info["has_prev_page"] = has_more
            page_info["has_next_page"] = self.ctx.last_id is not None
        else:
            page_info["has_next_page"] = has_more
            page_info["has_prev_page"] = self.ctx.last_id is not None

        self.results["page_info"] = page_info
