#!/usr/bin/env python
# -*- coding: utf-8 -*-
# aiosyslogd/web.py

from .config import load_config
from dataclasses import dataclass
from datetime import datetime, timedelta
from loguru import logger
from quart import Quart, render_template, request, abort, Response
from types import ModuleType
from typing import Any, Dict, List, Tuple
import aiosqlite
import asyncio
import glob
import os
import sqlite3
import sys
import time

uvloop: ModuleType | None = None
try:
    if sys.platform == "win32":
        import winloop as uvloop
    else:
        import uvloop
except ImportError:
    pass  # uvloop or winloop is an optional for speedup, not a requirement


# --- Globals & App Setup ---
CFG: Dict[str, Any] = load_config()
WEB_SERVER_CFG: Dict[str, Any] = CFG.get("web_server", {})
DEBUG: bool = WEB_SERVER_CFG.get("debug", False)

log_level: str = "DEBUG" if DEBUG else "INFO"
logger.remove()
logger.add(
    sys.stderr,
    format="[{time:YYYY-MM-DD HH:mm:ss ZZ}] [{process}] [{level}] {message}",
    level=log_level,
)

app: Quart = Quart(__name__)
app.jinja_env.add_extension("jinja2.ext.do")
app.logger = logger  # type: ignore[assignment]


# --- Datetime Type Adapters for SQLite ---
def adapt_datetime_iso(val: datetime) -> str:
    """Adapt datetime.datetime to timezone-aware ISO 8601 string."""
    return val.isoformat()


def convert_timestamp_iso(val: bytes) -> datetime:
    """Convert ISO 8601 string from DB back to a datetime.datetime object."""
    return datetime.fromisoformat(val.decode())


aiosqlite.register_adapter(datetime, adapt_datetime_iso)
aiosqlite.register_converter("TIMESTAMP", convert_timestamp_iso)


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


# --- Helper Functions ---
def get_available_databases() -> List[str]:
    """Finds available monthly SQLite database files."""
    db_template: str = (
        CFG.get("database", {})
        .get("sqlite", {})
        .get("database", "syslog.sqlite3")
    )
    base, ext = os.path.splitext(db_template)
    search_pattern: str = f"{base}_*{ext}"
    files: List[str] = glob.glob(search_pattern)
    files.sort(reverse=True)
    return files


# --- Core Logic in a Dedicated Class ---
class LogQuery:
    """Handles the logic for fetching and paginating logs from the database."""

    def __init__(self, context: QueryContext):
        """Initializes the LogQuery with a QueryContext."""
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
        # CORRECTED: Ensure the expression evaluates to a proper boolean
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
                await self._get_total_log_count()
                await self._fetch_log_page()
                self._prepare_pagination()

        except (aiosqlite.OperationalError, aiosqlite.DatabaseError) as e:
            self.results["error"] = str(e)
            logger.opt(exception=True).error(
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
            app.logger.debug("Using optimized approximate count.")
            start_id_for_count = (
                self.start_id if self.start_id is not None else 1
            )
            self.results["total_logs"] = (self.end_id - start_id_for_count) + 1
        else:
            app.logger.debug("Using standard COUNT(*) query.")
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


# --- Standalone Functions (could be moved to a utils file) ---
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
            f"Boundary Query (Start):\n  Result ID: {start_id}\n"
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
    params: List[Any] = []
    where_clauses: List[str] = []
    from_clause: str = "FROM SystemEvents"
    if start_id is not None:
        where_clauses.append("ID >= ?")
        params.append(start_id)
    if end_id is not None:
        where_clauses.append("ID <= ?")
        params.append(end_id)
    if filters.get("from_host"):
        where_clauses.append("FromHost = ?")
        params.append(filters["from_host"])
    if search_query:
        fts_where_parts: List[str] = ["Message MATCH ?"]
        fts_params: List[Any] = [search_query]
        if start_id is not None:
            fts_where_parts.append("rowid >= ?")
            fts_params.append(start_id)
        if end_id is not None:
            fts_where_parts.append("rowid <= ?")
            fts_params.append(end_id)
        fts_subquery = (
            "SELECT rowid FROM SystemEvents_FTS "
            f"WHERE {' AND '.join(fts_where_parts)}"
        )
        where_clauses.append(f"ID IN ({fts_subquery})")
        params.extend(fts_params)
    base_sql = "SELECT ID, FromHost, ReceivedAt, Message"
    count_sql = f"SELECT COUNT(*) {from_clause}"
    main_sql = f"{base_sql} {from_clause}"
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)
        count_sql += where_sql
        main_sql += where_sql
    count_params = list(params)
    main_params = list(params)
    order_by, id_comparison = (
        ("ASC", ">") if direction == "prev" else ("DESC", "<")
    )
    if last_id:
        main_sql += (
            f" {'AND' if where_clauses else 'WHERE'} ID {id_comparison} ?"
        )
        main_params.append(last_id)
    main_sql += f" ORDER BY ID {order_by} LIMIT {page_size + 1}"
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


# --- Main Application Logic ---
@app.before_serving
async def startup() -> None:
    """Initial setup before serving requests."""
    app.logger.info(
        f"{__name__.title()} is running with "
        f"{asyncio.get_event_loop_policy().__module__}."
    )


@app.route("/")
async def index() -> str | Response:
    """Main route for displaying and searching logs."""
    context: Dict[str, Any] = {
        "request": request,
        "available_dbs": get_available_databases(),
        "search_query": request.args.get("q", "").strip(),
        "filters": {
            key: request.args.get(key, "").strip()
            for key in ["from_host", "received_at_min", "received_at_max"]
        },
        "selected_db": None,
        "logs": [],
        "total_logs": 0,
        "error": None,
        "page_info": {
            "has_next_page": False,
            "next_last_id": None,
            "has_prev_page": False,
            "prev_last_id": None,
        },
        "debug_query": "",
        "query_time": 0.0,
    }

    if not context["available_dbs"]:
        context["error"] = (
            "No SQLite database files found. "
            "Ensure `aiosyslogd` has run and created logs."
        )
        return await render_template("index.html", **context)

    selected_db = request.args.get("db_file", context["available_dbs"][0])
    if selected_db not in context["available_dbs"]:
        abort(404, "Database file not found.")
    context["selected_db"] = selected_db

    start_time: float = time.perf_counter()

    query_context = QueryContext(
        db_path=selected_db,
        search_query=context["search_query"],
        filters=context["filters"],
        last_id=request.args.get("last_id", type=int),
        direction=request.args.get("direction", "next").strip(),
        page_size=50,
    )

    log_query = LogQuery(query_context)
    db_results = await log_query.run()

    context.update(
        {
            "logs": db_results["logs"],
            "total_logs": db_results["total_logs"],
            "page_info": db_results["page_info"],
            "debug_query": "\n\n---\n\n".join(db_results["debug_info"]),
            "error": db_results["error"],
            "query_time": time.perf_counter() - start_time,
        }
    )

    return await render_template("index.html", **context)


def check_backend() -> bool:
    """Checks if the backend database is compatible with the web UI."""
    db_driver: str | None = CFG.get("database", {}).get("driver")
    if db_driver == "meilisearch":
        logger.info("Meilisearch backend is selected.")
        logger.warning("This web UI is for the SQLite backend only.")
        return False
    return True


def main() -> None:
    """Main entry point for the web server."""
    if not check_backend():
        sys.exit(0)
    host: str = WEB_SERVER_CFG.get("bind_ip", "127.0.0.1")
    port: int = WEB_SERVER_CFG.get("bind_port", 5141)
    logger.info(f"Starting aiosyslogd-web interface on http://{host}:{port}")
    if uvloop:
        uvloop.install()
    app.run(host=host, port=port, debug=DEBUG)


if __name__ == "__main__":
    main()
