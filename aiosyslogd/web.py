#!/usr/bin/env python
# -*- coding: utf-8 -*-
# aiosyslogd/web.py

from datetime import datetime
from quart import Quart, render_template_string, request, abort
from types import ModuleType
import aiosqlite
import asyncio
import glob
import os
import sqlite3
import sys
import time
import toml

uvloop: ModuleType | None = None
try:
    if sys.platform == "win32":
        import winloop as uvloop
    else:
        import uvloop
except ImportError:
    pass  # uvloop or winloop is an optional for speedup, not a requiremen


# --- Configuration Loading ---
def load_config():
    """Loads configuration from a TOML file."""
    config_path_from_env = os.environ.get("AIOSYSLOGD_CONFIG")
    config_path = config_path_from_env or "aiosyslogd.toml"

    if not os.path.exists(config_path):
        print(f"Error: Configuration file not found at '{config_path}'")
        raise SystemExit(
            "Please run 'aiosyslogd' first to create a default config file."
        )

    try:
        with open(config_path, "r") as f:
            return toml.load(f)
    except toml.TomlDecodeError as e:
        print(f"Error decoding TOML file {config_path}: {e}")
        raise SystemExit("Aborting due to invalid configuration file.")


# --- Globals ---
CFG = load_config()
app = Quart(__name__)
# Enable the 'do' extension for the template environment
app.jinja_env.add_extension("jinja2.ext.do")


# --- Datetime Type Adapters for SQLite ---
# These functions ensure that datetime objects are correctly handled when reading from
# and writing to the database by converting them to and from ISO 8601 strings.
def adapt_datetime_iso(val):
    """Adapt datetime.datetime to timezone-aware ISO 8601 string."""
    return val.isoformat()


def convert_timestamp_iso(val):
    """Convert ISO 8601 string from DB back to a datetime.datetime object."""
    return datetime.fromisoformat(val.decode())


# Register the converters with the aiosqlite library globally.
aiosqlite.register_adapter(datetime, adapt_datetime_iso)
aiosqlite.register_converter("TIMESTAMP", convert_timestamp_iso)


# --- HTML Template with Alpine.js and Tailwind CSS ---
# Using a CDN for simplicity as requested.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>aiosyslogd Log Viewer</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: 'Inter', sans-serif; }
        .search-bar {
            box-shadow: 0 1px 6px rgba(32, 33, 36, 0.28);
            border-radius: 24px;
        }
        .search-bar:hover {
            box-shadow: 0 2px 8px rgba(32, 33, 36, 0.35);
        }
    </style>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet">
</head>
<body class="bg-gray-100 text-gray-800">

<div class="container mx-auto p-4 md:p-8">
    <header class="text-center mb-8">
        <h1 class="text-4xl font-bold text-gray-700">aiosyslogd Log Viewer</h1>
        <p class="text-gray-500">SQLite Log Search Interface</p>
    </header>

    <!-- Search and Filter Form -->
    <form action="{{ url_for('index') }}" method="get" class="bg-white p-6 rounded-lg shadow-md mb-8">
        <!-- Database Selector -->
        <div class="mb-4">
            <label for="db_file" class="block text-sm font-medium text-gray-700 mb-1">Database File</label>
            <select name="db_file" id="db_file" class="w-full p-2 border border-gray-300 rounded-md shadow-sm focus:ring-indigo-500 focus:border-indigo-500" onchange="this.form.submit()">
                {% for db in available_dbs %}
                    <option value="{{ db }}" {% if db == selected_db %}selected{% endif %}>{{ db }}</option>
                {% endfor %}
            </select>
        </div>

        <!-- Main Search Bar -->
        <div class="flex items-center space-x-2 mb-4">
            <div class="relative w-full">
                <input type="text" name="q" value="{{ search_query }}" placeholder="Enter FTS5 query for Message (e.g., 'error* OR failure')" class="w-full py-3 pl-4 pr-12 text-lg border-gray-300 search-bar focus:ring-indigo-500 focus:border-indigo-500">
                <div class="absolute inset-y-0 right-0 flex py-1.5 pr-1.5">
                    <button type="submit" class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-full shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
                        <svg class="h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                        </svg>
                    </button>
                </div>
            </div>
            <a href="{{ url_for('index', db_file=selected_db) }}" class="flex-shrink-0 inline-flex items-center justify-center h-12 w-12 border border-gray-300 rounded-full text-gray-500 bg-white hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500" title="Reset Search and Filters">
                <svg class="h-6 w-6" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
            </a>
        </div>

        <!-- Advanced Filters -->
        <h3 class="text-lg font-medium text-gray-800 mb-2">Advanced Filters</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 border-t pt-4">
            <input type="hidden" name="last_id" value=""> <!-- Clear last_id when filters change -->
            <div>
                <label for="from_host" class="block text-sm font-medium text-gray-700">FromHost</label>
                <input type="text" name="from_host" id="from_host" value="{{ filters.from_host or '' }}" class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50">
            </div>
            <div>
                <label for="received_at_min" class="block text-sm font-medium text-gray-700">Received At (Start)</label>
                <input type="datetime-local" name="received_at_min" id="received_at_min" value="{{ filters.received_at_min or '' }}" class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50">
            </div>
            <div>
                <label for="received_at_max" class="block text-sm font-medium text-gray-700">Received At (End)</label>
                <input type="datetime-local" name="received_at_max" id="received_at_max" value="{{ filters.received_at_max or '' }}" class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50">
            </div>
        </div>
    </form>

    <!-- Debug SQL Query -->
    {% if debug_query %}
    <div class="bg-gray-800 text-white p-4 rounded-lg shadow-md mb-8">
        <h3 class="font-bold mb-2 text-gray-400 uppercase tracking-wider text-xs">Executed SQL Query</h3>
        <pre class="font-mono text-sm whitespace-pre-wrap"><code>{{ debug_query }}</code></pre>
    </div>
    {% endif %}

    <!-- Results Count -->
    {% if total_logs is not none %}
    <div class="mb-4 text-gray-600">
        Found <span class="font-bold">{{ "{:,}".format(total_logs) }}</span> matching logs
        {% if query_time is not none %}
         in <span class="font-bold">{{ "%.3f"|format(query_time) }}s</span>.
        {% endif %}
    </div>
    {% endif %}

    <!-- Results -->
    {% if error %}
        <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded-lg relative" role="alert">
            <strong class="font-bold">Query Error:</strong>
            <span class="block sm:inline">{{ error }}</span>
        </div>
    {% elif logs %}
        <div class="bg-white shadow-md rounded-lg overflow-x-auto">
            <table class="min-w-full divide-y divide-gray-200">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ID</th>
                        <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Received At</th>
                        <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">From Host</th>
                        <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Message</th>
                    </tr>
                </thead>
                <tbody class="bg-white divide-y divide-gray-200">
                    {% for log in logs %}
                    <tr>
                        <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">{{ log.ID }}</td>
                        <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">{{ log.ReceivedAt.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                        <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">{{ log.FromHost }}</td>
                        <td class="px-4 py-4 text-sm text-gray-900 break-all">{{ log.Message }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    {% else %}
        <div class="text-center py-12 bg-white rounded-lg shadow-md">
            <p class="text-gray-500">No logs found. Try adjusting your search.</p>
        </div>
    {% endif %}

    <!-- Pagination -->
    <div class="flex justify-between items-center mt-6">
        <div>
           {% if logs and page_info.prev_last_id %}
             {% set prev_args = request.args.to_dict() %}
             {% do prev_args.update({'last_id': page_info.prev_last_id, 'direction': 'prev'}) %}
             <a href="{{ url_for('index', **prev_args) }}" class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">
                Previous
            </a>
            {% endif %}
        </div>
        <div>
            {% if page_info.has_next_page %}
            {% set next_args = request.args.to_dict() %}
            {% do next_args.update({'last_id': page_info.next_last_id}) %}
            <a href="{{ url_for('index', **next_args) }}" class="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50">
                Next
            </a>
            {% endif %}
        </div>
    </div>
</div>

</body>
</html>
"""


def get_available_databases():
    """Finds available monthly SQLite database files."""
    db_template = (
        CFG.get("database", {})
        .get("sqlite", {})
        .get("database", "syslog.sqlite3")
    )
    base, ext = os.path.splitext(db_template)
    search_pattern = f"{base}_*{ext}"
    files = glob.glob(search_pattern)
    # Sort files descending by name (which corresponds to date)
    files.sort(reverse=True)
    return files


@app.before_serving
async def startup():
    """Checks the database driver before starting."""
    driver = CFG.get("database", {}).get("driver")
    if driver == "meilisearch":
        print("---")
        print("Meilisearch backend is an alpha feature.")
        print("Please use Meilisearch's own development web UI for searching.")
        print("---")


@app.route("/")
async def index():
    """Main route for displaying and searching logs."""
    available_dbs = get_available_databases()
    if not available_dbs:
        return await render_template_string(
            HTML_TEMPLATE,
            error="No SQLite database files found. Ensure `aiosyslogd` has run and created logs.",
        )

    # --- Get parameters from request ---
    selected_db = request.args.get("db_file", available_dbs[0])
    search_query = request.args.get("q", "").strip()
    last_id = request.args.get("last_id", type=int)

    page_size = 50

    filters = {
        "from_host": request.args.get("from_host", "").strip(),
        "received_at_min": request.args.get("received_at_min", "").strip(),
        "received_at_max": request.args.get("received_at_max", "").strip(),
    }

    if selected_db not in available_dbs:
        abort(404, "Database file not found.")

    # --- Build SQL Query based on optimal patterns ---
    query_error = None

    has_from_host = bool(filters["from_host"])
    has_time_range = bool(
        filters["received_at_min"] or filters["received_at_max"]
    )
    has_fts = bool(search_query)

    from_clause = "FROM SystemEvents"
    where_clauses = []
    params = []

    def apply_fts_subquery():
        """Applies the FTS subquery for the Message field."""
        where_clauses.append(
            "ID IN (SELECT rowid FROM SystemEvents_FTS WHERE Message MATCH ?)"
        )
        params.append(search_query)

    # This refactored block implements the user's highly optimized query patterns.
    # It prioritizes the most selective index first.
    if has_from_host and has_time_range:
        # Scenario 4 (Most Complex): FromHost + Time + optional FTS
        from_clause += " INDEXED BY idx_SystemEvents_FromHost"
        where_clauses.append("FromHost = ?")
        params.append(filters["from_host"])

        # Build the subquery for time and FTS filters
        subquery_parts = []
        subquery_params = []

        time_subquery = "SELECT ID FROM SystemEvents INDEXED BY idx_SystemEvents_ReceivedAt WHERE "
        time_conditions = []
        if filters["received_at_min"]:
            time_conditions.append("ReceivedAt >= ?")
            subquery_params.append(filters["received_at_min"].replace("T", " "))
        if filters["received_at_max"]:
            time_conditions.append("ReceivedAt <= ?")
            subquery_params.append(filters["received_at_max"].replace("T", " "))
        time_subquery += " AND ".join(time_conditions)
        subquery_parts.append(time_subquery)

        if has_fts:
            subquery_parts.append(
                "INTERSECT SELECT rowid FROM SystemEvents_FTS WHERE Message MATCH ?"
            )
            subquery_params.append(search_query)

        full_subquery = " ".join(subquery_parts)
        where_clauses.append(f"ID IN ({full_subquery})")
        params.extend(subquery_params)

    elif has_from_host:
        # Scenario 1: FromHost only (+ optional FTS)
        from_clause += " INDEXED BY idx_SystemEvents_FromHost"
        where_clauses.append("FromHost = ?")
        params.append(filters["from_host"])
        if has_fts:
            apply_fts_subquery()

    elif has_time_range:
        # Scenario 2: Time range only (+ optional FTS)
        from_clause += " INDEXED BY idx_SystemEvents_ReceivedAt"
        if filters["received_at_min"]:
            where_clauses.append("ReceivedAt >= ?")
            params.append(filters["received_at_min"].replace("T", " "))
        if filters["received_at_max"]:
            where_clauses.append("ReceivedAt <= ?")
            params.append(filters["received_at_max"].replace("T", " "))
        if has_fts:
            apply_fts_subquery()

    elif has_fts:
        # Scenario 5: FTS only
        apply_fts_subquery()

    # --- Construct and Execute Queries ---
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

    # Add pagination to the main query only
    if last_id:
        main_sql += " AND ID < ?" if where_clauses else " WHERE ID < ?"
        main_params.append(last_id)

    main_sql += f" ORDER BY ID DESC LIMIT {page_size + 1}"

    debug_query_string = f"Query: {main_sql}\n\nParameters: {main_params}"

    logs = []
    total_logs = None
    query_time = None
    if not query_error:
        try:
            start_time = time.perf_counter()
            db_uri = f"file:{selected_db}?mode=ro"
            conn = await aiosqlite.connect(
                db_uri,
                uri=True,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            )
            conn.row_factory = aiosqlite.Row

            async with conn.execute(count_sql, count_params) as cursor:
                total_logs = (await cursor.fetchone())[0]

            async with conn.execute(main_sql, main_params) as cursor:
                logs = await cursor.fetchall()

            await conn.close()
            query_time = time.perf_counter() - start_time
        except (aiosqlite.OperationalError, aiosqlite.DatabaseError) as e:
            query_error = str(e)

    # --- Prepare Pagination Info ---
    has_next_page = len(logs) > page_size
    next_last_id = logs[page_size - 1]["ID"] if logs and has_next_page else None

    page_info = {
        "has_next_page": has_next_page,
        "next_last_id": next_last_id,
        "prev_last_id": None,
    }

    return await render_template_string(
        HTML_TEMPLATE,
        logs=logs[:page_size],
        total_logs=total_logs,
        query_time=query_time,
        search_query=search_query,
        available_dbs=available_dbs,
        selected_db=selected_db,
        error=query_error,
        page_info=page_info,
        filters=filters,
        request=request,
        debug_query=debug_query_string,
    )


def main():
    """CLI Entry point to run the web server."""
    db_driver = CFG.get("database", {}).get("driver", "sqlite")
    if db_driver == "meilisearch":
        print("Meilisearch backend is selected.")
        print("This web UI is for the SQLite backend only.")
        print("Please use Meilisearch's own development web UI for searching.")
        raise SystemExit()

    server_cfg = CFG.get("web_server", {})
    host = server_cfg.get("bind_ip", "127.0.0.1")
    port = server_cfg.get("bind_port", 5141)
    debug = server_cfg.get("debug", False)

    print(f"Starting aiosyslogd-web interface on http://{host}:{port}")
    if uvloop:
        uvloop.install()
    app.run(host=host, port=port, debug=debug, loop=asyncio.get_event_loop())


if __name__ == "__main__":
    main()
