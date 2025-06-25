#!/usr/bin/env python
# -*- coding: utf-8 -*-
# aiosyslogd/web.py

from .config import load_config
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


# --- Globals ---
CFG: Dict[str, Any] = load_config()
WEB_SERVER_CFG: Dict[str, Any] = CFG.get("web_server", {})
DEBUG: bool = WEB_SERVER_CFG.get("debug", False)

# --- Logger Configuration ---
# Configure the logger output format to match Quart default format.
log_level = "DEBUG" if DEBUG else "INFO"
logger.remove()
logger.add(
    sys.stderr,
    format="[{time:YYYY-MM-DD HH:mm:ss ZZ}] [{process}] [{level}] {message}",
    level=log_level,
)

# --- Quart Application ---
app: Quart = Quart(__name__)
# Enable the 'do' extension for the template environment
app.jinja_env.add_extension("jinja2.ext.do")
# Replace Quart's logger with our configured logger.
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


async def get_time_boundary_ids(
    conn: aiosqlite.Connection, min_time_filter: str, max_time_filter: str
) -> Tuple[int | None, int | None, List[str]]:
    """
    Finds the starting and ending log IDs for a given time window using an
    efficient, iterative, chunk-based search.
    """
    start_id: int | None = None
    end_id: int | None = None
    debug_queries: List[str] = []

    db_time_format = "%Y-%m-%d %H:%M:%S"
    chunk_sizes_minutes = [5, 15, 30, 60]

    def _parse_time_string(time_str: str) -> datetime:
        """Parses a time string which may or may not include seconds."""
        time_str = time_str.replace("T", " ")
        try:
            return datetime.strptime(time_str, db_time_format)
        except ValueError:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M")

    # --- Find Start ID ---
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

            start_sql = "SELECT ID FROM SystemEvents WHERE ReceivedAt >= ? AND ReceivedAt < ? ORDER BY ID ASC LIMIT 1"
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
                f"  - Chunk ({minutes_to_add}m): {start_params} -> Found: {start_id is not None} ({elapsed_ms:.2f}ms)"
            )
            current_start_dt = chunk_end_dt
            chunk_index += 1

        debug_queries.append(
            f"Boundary Query (Start):\n  Result ID: {start_id}\n  Total Time: {total_start_time_ms:.2f}ms\n"
            + "\n".join(start_debug_chunks)
        )

    # --- Find End ID ---
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

            end_boundary_sql = "SELECT ID FROM SystemEvents WHERE ReceivedAt > ? AND ReceivedAt < ? ORDER BY ID ASC LIMIT 1"
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
                f"  - Chunk ({minutes_to_add}m): {end_params} -> Found: {row is not None} ({elapsed_ms:.2f}ms)"
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
            # Fallback if no logs exist after the end time.
            # This query finds the last log within the complete time window.
            fallback_clauses = ["ReceivedAt <= ?"]
            fallback_params = [end_dt.strftime(db_time_format)]

            if min_time_filter:
                min_dt = _parse_time_string(min_time_filter)
                fallback_clauses.append("ReceivedAt >= ?")
                fallback_params.append(min_dt.strftime(db_time_format))

            fallback_sql = f"SELECT MAX(ID) FROM SystemEvents WHERE {' AND '.join(fallback_clauses)}"

            start_time = time.perf_counter()
            async with conn.execute(
                fallback_sql, tuple(fallback_params)
            ) as cursor:
                row = await cursor.fetchone()
                end_id = row[0] if row else None
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            total_end_time_ms += elapsed_ms
            end_debug_chunks.append(
                f"  - Fallback MAX(ID) Query -> Found: {end_id is not None} ({elapsed_ms:.2f}ms)"
            )

        debug_queries.append(
            f"Boundary Query (End):\n  Calculated End ID: {end_id}\n  Total Time: {total_end_time_ms:.2f}ms\n"
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
    """Builds the main and count SQL queries based on filters and direction."""
    params: List[Any] = []
    where_clauses: List[str] = []
    from_clause: str = "FROM SystemEvents"

    # Time-based ID range filter for the main query
    if start_id is not None:
        where_clauses.append("ID >= ?")
        params.append(start_id)
    if end_id is not None:
        where_clauses.append("ID <= ?")
        params.append(end_id)

    # Other attribute filters
    if filters["from_host"]:
        where_clauses.append("FromHost = ?")
        params.append(filters["from_host"])

    # FTS subquery filter
    if search_query:
        # Optimization: Apply the ID range to the FTS subquery as well.
        # This significantly narrows the FTS search space.
        fts_where_parts: List[str] = ["Message MATCH ?"]
        fts_params: List[str | int] = [search_query]

        if start_id is not None:
            fts_where_parts.append("rowid >= ?")
            fts_params.append(start_id)
        if end_id is not None:
            fts_where_parts.append("rowid <= ?")
            fts_params.append(end_id)

        fts_where_clause = " AND ".join(fts_where_parts)
        fts_subquery = (
            f"SELECT rowid FROM SystemEvents_FTS WHERE {fts_where_clause}"
        )

        where_clauses.append(f"ID IN ({fts_subquery})")
        params.extend(fts_params)

    base_sql = "SELECT ID, FromHost, ReceivedAt, Message"
    count_sql = f"SELECT COUNT(*) {from_clause}"
    main_sql = f"{base_sql} {from_clause}"

    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)
        count_sql += where_sql
        main_sql += where_sql

    count_params = list(params)
    main_params = list(params)

    # --- Pagination Logic ---
    order_by = "DESC"
    id_comparison = "<"
    if direction == "prev":
        order_by = "ASC"
        id_comparison = ">"

    if last_id:
        paginator_keyword = "AND" if where_clauses else "WHERE"
        main_sql += f" {paginator_keyword} ID {id_comparison} ?"
        main_params.append(last_id)

    main_sql += f" ORDER BY ID {order_by} LIMIT {page_size + 1}"

    return {
        "main_sql": main_sql,
        "main_params": main_params,
        "count_sql": count_sql,
        "count_params": count_params,
        "debug_query": f"Main Query:\n  Query: {main_sql}\n  Parameters: {main_params}",
    }


async def fetch_logs_from_db(
    db_path: str,
    search_query: str,
    filters: Dict[str, Any],
    last_id: int | None,
    direction: str,
    page_size: int,
) -> Dict[str, Any]:
    """
    Connects to the database, executes the appropriate log query,
    and returns the results and pagination info.
    """
    results: Dict[str, Any] = {
        "logs": [],
        "total_logs": 0,
        "page_info": {},
        "debug_info": [],
        "error": None,
    }
    start_id: int | None = None
    end_id: int | None = None

    # Define the optimization scenario for count approximation
    use_approximate_count = (
        not search_query
        and not filters["from_host"]
        and (filters["received_at_min"] or filters["received_at_max"])
    )

    try:
        db_uri = f"file:{db_path}?mode=ro"
        async with aiosqlite.connect(
            db_uri,
            uri=True,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        ) as conn:
            conn.row_factory = aiosqlite.Row

            min_time_filter = filters["received_at_min"]
            max_time_filter = filters["received_at_max"]

            # Boundary ID calculation is always useful
            if min_time_filter or max_time_filter:
                start_id, end_id, boundary_queries = (
                    await get_time_boundary_ids(
                        conn, min_time_filter, max_time_filter
                    )
                )
                results["debug_info"].extend(boundary_queries)

            # --- COUNTING STEP (Optimized or Standard) ---
            if use_approximate_count and end_id is not None:
                app.logger.debug("Using optimized approximate count.")
                start_id_for_count = start_id if start_id is not None else 1
                results["total_logs"] = (end_id - start_id_for_count) + 1
            else:
                app.logger.debug("Using standard COUNT(*) query.")
                # Build a temporary query part just for the count
                count_query_parts = build_log_query(
                    search_query, filters, None, 0, "next", start_id, end_id
                )
                async with conn.execute(
                    count_query_parts["count_sql"],
                    count_query_parts["count_params"],
                ) as cursor:
                    count_result = await cursor.fetchone()
                    if count_result:
                        results["total_logs"] = count_result[0]

            # --- LOG FETCHING STEP ---
            # For the first page of a simple time query, we adjust the start_id for performance.
            effective_start_id = start_id
            if use_approximate_count and last_id is None and end_id is not None:
                effective_start_id = max(
                    start_id or 1, end_id - page_size - 50
                )  # Widen the chunk slightly
                results["debug_info"].append(
                    f"Applied fast-path adjustment to start_id: {effective_start_id}"
                )

            query_parts = build_log_query(
                search_query,
                filters,
                last_id,
                page_size,
                direction,
                effective_start_id,
                end_id,
            )
            results["debug_info"].append(query_parts["debug_query"])

            async with conn.execute(
                query_parts["main_sql"], query_parts["main_params"]
            ) as cursor:
                results["logs"] = await cursor.fetchall()

            # --- PAGINATION LOGIC (Common to both paths) ---
            if direction == "prev":
                results["logs"].reverse()

            has_more = len(results["logs"]) > page_size
            results["logs"] = results["logs"][:page_size]

            results["page_info"] = {
                "has_next_page": False,
                "next_last_id": (
                    results["logs"][-1]["ID"] if results["logs"] else None
                ),
                "has_prev_page": False,
                "prev_last_id": (
                    results["logs"][0]["ID"] if results["logs"] else None
                ),
            }
            if direction == "prev":
                results["page_info"]["has_prev_page"] = has_more
                results["page_info"]["has_next_page"] = last_id is not None
            else:
                results["page_info"]["has_next_page"] = has_more
                results["page_info"]["has_prev_page"] = last_id is not None

    except (aiosqlite.OperationalError, aiosqlite.DatabaseError) as e:
        results["error"] = str(e)
        logger.opt(exception=True).error(f"Database query failed for {db_path}")

    return results


@app.before_serving
async def startup() -> None:
    """Function to run actions before the server starts serving."""
    # Verify the running event loop policy
    app.logger.info(
        f"{__name__.title()} is running with "
        f"{asyncio.get_event_loop_policy().__module__}."
    )


@app.route("/")
async def index() -> str | Response:
    """Main route for displaying and searching logs."""
    context: Dict[str, Any] = {
        "search_query": request.args.get("q", "").strip(),
        "available_dbs": get_available_databases(),
        "error": None,
        "selected_db": None,
        "filters": {
            key: request.args.get(key, "").strip()
            for key in ["from_host", "received_at_min", "received_at_max"]
        },
        "request": request,
        # Pre-initialize all keys the template might need
        "logs": [],
        "total_logs": 0,
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

    # --- Get parameters from request ---
    selected_db = request.args.get("db_file", context["available_dbs"][0])
    if selected_db not in context["available_dbs"]:
        abort(404, "Database file not found.")
    context["selected_db"] = selected_db

    last_id: int | None = request.args.get("last_id", type=int)
    direction: str = request.args.get("direction", "next").strip()
    page_size: int = 50

    start_time: float = time.perf_counter()

    # --- Fetch data from the database using the dedicated function ---
    db_results = await fetch_logs_from_db(
        db_path=selected_db,
        search_query=context["search_query"],
        filters=context["filters"],
        last_id=last_id,
        direction=direction,
        page_size=page_size,
    )

    # --- Update context with results for rendering ---
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
    db_driver: str | None = CFG.get("database", {}).get("driver")
    if db_driver == "meilisearch":
        logger.info("Meilisearch backend is selected.")
        logger.warning("This web UI is for the SQLite backend only.")
        logger.warning(
            "Please use Meilisearch's own development web UI for searching."
        )
        return False
    return True


def main() -> None:
    """CLI Entry point to run the web server."""
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
