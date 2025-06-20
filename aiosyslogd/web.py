#!/usr/bin/env python
# -*- coding: utf-8 -*-
# aiosyslogd/web.py

from .config import load_config
from datetime import datetime
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
    Finds the starting and ending log IDs for a given time window.
    Also returns the queries used for debugging purposes.
    """
    start_id: int | None = None  # noqa
    end_id: int | None = None  # noqa
    debug_queries: List[str] = []

    where_parts = []
    params = []

    if min_time_filter:
        where_parts.append("ReceivedAt >= ?")
        params.append(min_time_filter.replace("T", " "))
    if max_time_filter:
        where_parts.append("ReceivedAt <= ?")
        params.append(max_time_filter.replace("T", " "))

    if not where_parts:
        return None, None, []

    where_clause = " AND ".join(where_parts)

    # Find start_id
    start_sql = f"SELECT ID FROM SystemEvents WHERE {where_clause} ORDER BY ID ASC LIMIT 1"
    debug_queries.append(
        f"Boundary Query (Start):\nQuery: {start_sql}\nParameters: {tuple(params)}"
    )
    async with conn.execute(start_sql, params) as cursor:
        row = await cursor.fetchone()
        start_id = row["ID"] if row else None

    # If no start_id is found, there are no records in the range, so we can stop.
    if start_id is None:
        return None, None, debug_queries

    # Find end_id
    end_sql = f"SELECT ID FROM SystemEvents WHERE {where_clause} ORDER BY ID DESC LIMIT 1"
    debug_queries.append(
        f"Boundary Query (End):\nQuery: {end_sql}\nParameters: {tuple(params)}"
    )
    async with conn.execute(end_sql, params) as cursor:
        row = await cursor.fetchone()
        end_id = row["ID"] if row else None

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
        "debug_query": f"Main Query:\nQuery: {main_sql}\nParameters: {main_params}",
    }


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
    show_sql = request.args.get("show_sql", "0") == "1"
    context: Dict[str, Any] = {
        "logs": [],
        "total_logs": 0,
        "query_time": 0.0,
        "search_query": request.args.get("q", "").strip(),
        "available_dbs": get_available_databases(),
        "selected_db": None,
        "error": None,
        "page_info": {},
        "filters": {
            key: request.args.get(key, "").strip()
            for key in ["from_host", "received_at_min", "received_at_max"]
        },
        "debug_query": "",
        "request": request,
        "show_sql": show_sql,
    }

    if not context["available_dbs"]:
        context["error"] = (
            "No SQLite database files found. "
            "Ensure `aiosyslogd` has run and created logs."
        )
        return await render_template("index.html", **context)

    # --- Get parameters from request ---
    context["selected_db"] = request.args.get(
        "db_file", context["available_dbs"][0]
    )
    last_id: int | None = request.args.get("last_id", type=int)
    direction: str = request.args.get("direction", "next").strip()
    page_size: int = 50

    if context["selected_db"] not in context["available_dbs"]:
        abort(404, "Database file not found.")

    start_id: int | None = None
    end_id: int | None = None
    debug_info: List[str] = []

    # --- Execute Query ---
    try:
        start_time: float = time.perf_counter()
        db_uri: str = f"file:{context['selected_db']}?mode=ro"
        async with aiosqlite.connect(
            db_uri,
            uri=True,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        ) as conn:
            conn.row_factory = aiosqlite.Row

            min_time_filter = context["filters"]["received_at_min"]
            max_time_filter = context["filters"]["received_at_max"]

            if min_time_filter or max_time_filter:
                start_id, end_id, boundary_queries = (
                    await get_time_boundary_ids(
                        conn, min_time_filter, max_time_filter
                    )
                )
                debug_info.extend(boundary_queries)

                # Early exit: If a time boundary was set but no logs were found for it,
                # the result set is empty.
                if (min_time_filter and start_id is None) or (
                    max_time_filter and end_id is None
                ):
                    context["logs"], context["total_logs"] = [], 0
                    context["query_time"] = time.perf_counter() - start_time
                    context["debug_query"] = "\n\n---\n\n".join(debug_info)
                    context["page_info"] = {
                        "has_next_page": False,
                        "next_last_id": None,
                        "has_prev_page": False,
                        "prev_last_id": None,
                    }
                    return await render_template("index.html", **context)

            # --- Build and Execute Queries ---
            query_parts = build_log_query(
                context["search_query"],
                context["filters"],
                last_id,
                page_size,
                direction,
                start_id,
                end_id,
            )
            debug_info.append(query_parts["debug_query"])

            async with conn.execute(
                query_parts["count_sql"], query_parts["count_params"]
            ) as cursor:
                result = await cursor.fetchone()
                if result:
                    context["total_logs"] = result[0]
            async with conn.execute(
                query_parts["main_sql"], query_parts["main_params"]
            ) as cursor:
                context["logs"] = list(await cursor.fetchall())

        context["query_time"] = time.perf_counter() - start_time
        context["debug_query"] = "\n\n---\n\n".join(debug_info)

    except (aiosqlite.OperationalError, aiosqlite.DatabaseError) as e:
        context["error"] = str(e)
        app.logger.opt(exception=True).error(  # type: ignore[attr-defined]
            f"Database query failed for {context['selected_db']}"
        )

    # --- Prepare Pagination & Rendering ---
    if direction == "prev":
        context["logs"].reverse()

    has_more = len(context["logs"]) > page_size
    context["logs"] = context["logs"][:page_size]

    page_info = {
        "has_next_page": False,
        "next_last_id": context["logs"][-1]["ID"] if context["logs"] else None,
        "has_prev_page": False,
        "prev_last_id": context["logs"][0]["ID"] if context["logs"] else None,
    }

    if direction == "prev":
        page_info["has_prev_page"] = has_more
        page_info["has_next_page"] = True
    else:  # 'next' direction
        page_info["has_next_page"] = has_more
        page_info["has_prev_page"] = last_id is not None

    context["page_info"] = page_info

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
