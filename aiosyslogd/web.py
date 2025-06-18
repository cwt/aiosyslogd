#!/usr/bin/env python
# -*- coding: utf-8 -*-
# aiosyslogd/web.py

from .config import load_config
from datetime import datetime
from loguru import logger
from quart import Quart, render_template, request, abort, Response
from types import ModuleType
from typing import Any, Dict, List
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


def build_log_query(
    search_query: str,
    filters: Dict[str, str],
    last_id: int | None,
    page_size: int,
) -> Dict[str, Any]:
    """Builds the main and count SQL queries based on filters."""

    def _add_time_filters(
        conditions_list: List[str],
        params_list: List[str | int],
        time_filters: Dict[str, str],
    ) -> None:
        """Appends time-based WHERE conditions and parameters to the given lists."""
        # Format HTML datetime-local string to SQLite TIMESTAMP format
        if time_filters["received_at_min"]:
            conditions_list.append("ReceivedAt >= ?")
            params_list.append(
                time_filters["received_at_min"].replace("T", " ")
            )
        if time_filters["received_at_max"]:
            conditions_list.append("ReceivedAt <= ?")
            params_list.append(
                time_filters["received_at_max"].replace("T", " ")
            )

    params: List[str | int] = []
    has_from_host: bool = bool(filters["from_host"])
    has_time_range: bool = bool(
        filters["received_at_min"] or filters["received_at_max"]
    )
    has_fts: bool = bool(search_query)

    from_clause: str = "FROM SystemEvents"
    where_clauses: List[str] = []

    def apply_fts_subquery() -> None:
        """Applies the FTS subquery for full-text search."""
        nonlocal where_clauses, params
        where_clauses.append(
            "ID IN (SELECT rowid FROM SystemEvents_FTS WHERE Message MATCH ?)"
        )
        params.append(search_query)

    if has_from_host and has_time_range:
        from_clause += " INDEXED BY idx_SystemEvents_FromHost"
        where_clauses.append("FromHost = ?")
        params.append(filters["from_host"])
        subquery_parts: List[str] = []
        subquery_params: List[str | int] = []

        time_subquery = (
            "SELECT ID FROM SystemEvents "
            "INDEXED BY idx_SystemEvents_ReceivedAt WHERE "
        )
        time_conditions: List[str] = []
        _add_time_filters(time_conditions, subquery_params, filters)
        time_subquery += " AND ".join(time_conditions)

        subquery_parts.append(time_subquery)
        if has_fts:
            subquery_parts.append(
                "INTERSECT SELECT rowid "
                "FROM SystemEvents_FTS "
                "WHERE Message MATCH ?"
            )
            subquery_params.append(search_query)
        full_subquery = " ".join(subquery_parts)
        where_clauses.append(f"ID IN ({full_subquery})")
        params.extend(subquery_params)
    elif has_from_host:
        from_clause += " INDEXED BY idx_SystemEvents_FromHost"
        where_clauses.append("FromHost = ?")
        params.append(filters["from_host"])
        if has_fts:
            apply_fts_subquery()
    elif has_time_range:
        from_clause += " INDEXED BY idx_SystemEvents_ReceivedAt"
        _add_time_filters(where_clauses, params, filters)
        if has_fts:
            apply_fts_subquery()
    elif has_fts:
        apply_fts_subquery()

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

    if last_id:
        main_sql += " AND ID < ?" if where_clauses else " WHERE ID < ?"
        main_params.append(last_id)

    main_sql += f" ORDER BY ID DESC LIMIT {page_size + 1}"

    return {
        "main_sql": main_sql,
        "main_params": main_params,
        "count_sql": count_sql,
        "count_params": count_params,
        "debug_query": f"Query: {main_sql}\n\nParameters: {main_params}",
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
    page_size: int = 50

    if context["selected_db"] not in context["available_dbs"]:
        abort(404, "Database file not found.")

    # --- Build Query ---
    query_parts = build_log_query(
        context["search_query"], context["filters"], last_id, page_size
    )
    context["debug_query"] = query_parts["debug_query"]

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
            async with conn.execute(
                query_parts["count_sql"], query_parts["count_params"]
            ) as cursor:
                result = await cursor.fetchone()
                if result:
                    context["total_logs"] = result[0]
            async with conn.execute(
                query_parts["main_sql"], query_parts["main_params"]
            ) as cursor:
                context["logs"] = await cursor.fetchall()
        context["query_time"] = time.perf_counter() - start_time
    except (aiosqlite.OperationalError, aiosqlite.DatabaseError) as e:
        context["error"] = str(e)
        app.logger.opt(exception=True).error(  # type: ignore[attr-defined]
            f"Database query failed for {context['selected_db']}"
        )

    # --- Prepare Pagination & Rendering ---
    has_next_page: bool = len(context["logs"]) > page_size
    next_last_id: int | None = (
        context["logs"][page_size - 1]["ID"]
        if context["logs"] and has_next_page
        else None
    )
    context["page_info"] = {
        "has_next_page": has_next_page,
        "next_last_id": next_last_id,
        "prev_last_id": None,
    }
    context["logs"] = context["logs"][:page_size]

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
