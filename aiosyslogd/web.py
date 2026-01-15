#!/usr/bin/env python
# -*- coding: utf-8 -*-
# aiosyslogd/web.py

from .config import load_config
from .auth import AuthManager
from .db.logs_utils import redact
from .db.sqlite_utils import get_available_databases, QueryContext, LogQuery
from datetime import datetime, timedelta
from functools import wraps
from loguru import logger
from quart import (
    Quart,
    render_template,
    request,
    abort,
    Response,
    session,
    redirect,
    url_for,
    flash,
)
from types import ModuleType
from typing import Any, Dict, Generator
import aiosqlite
import asyncio
import os
import sys
import time
import argparse

uvloop: ModuleType | None = None
try:
    if sys.platform == "win32":
        import winloop as uvloop  # type: ignore
    else:
        import uvloop  # type: ignore
except ImportError:
    pass  # uvloop or winloop is an optional for speedup, not a requirement.


# --- Globals & App Setup ---
CFG: Dict[str, Any] = load_config()
WEB_SERVER_CFG: Dict[str, Any] = CFG.get("web_server", {})
DEBUG: bool = WEB_SERVER_CFG.get("debug", False)
REDACT: bool = WEB_SERVER_CFG.get("redact", False)

# Configure the loguru logger with Quart formatting.
log_level: str = "DEBUG" if DEBUG else "INFO"
logger.remove()
logger.add(
    sys.stderr,
    format="[{time:YYYY-MM-DD HH:mm:ss ZZ}] [{process}] [{level}] {message}",
    level=log_level,
)

# Create a Quart application instance.
app: Quart = Quart(__name__)
app.secret_key = os.urandom(24)
# Enable the 'do' extension for Jinja2.
app.jinja_env.add_extension("jinja2.ext.do")
# Replace the default Quart logger with loguru logger.
app.logger = logger  # type: ignore[assignment]
auth_manager = AuthManager(WEB_SERVER_CFG.get("users_file", "users.json"))


# --- Datetime Type Adapters for SQLite ---
def adapt_datetime_iso(val: datetime) -> str:
    """Adapt datetime.datetime to timezone-aware ISO 8601 string."""
    return val.isoformat()


def convert_timestamp_iso(val: bytes) -> datetime:
    """Convert ISO 8601 string from DB back to a datetime.datetime object."""
    return datetime.fromisoformat(val.decode())


# Registering the adapters and converters for aiosqlite.
aiosqlite.register_adapter(datetime, adapt_datetime_iso)
aiosqlite.register_converter("TIMESTAMP", convert_timestamp_iso)


# --- Auth ---
def login_required(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        user = auth_manager.get_user(session["username"])
        if not user or not user.is_enabled:
            session.pop("username", None)
            await flash("User disabled or does not exist.", "error")
            return redirect(url_for("login"))
        return await f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        user = auth_manager.get_user(session["username"])
        if not user.is_admin:
            await flash(
                "You do not have permission to access this page.", "error"
            )
            return redirect(url_for("index"))
        return await f(*args, **kwargs)

    return decorated_function


@app.context_processor
def inject_user():
    if "username" in session:
        user = auth_manager.get_user(session["username"])
        return dict(current_user=user)
    return dict(current_user=None)


@app.route("/login", methods=["GET", "POST"])
async def login():
    if request.method == "POST":
        form = await request.form
        username = form.get("username")
        password = form.get("password")
        if auth_manager.check_password(username, password):
            session["username"] = username
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        else:
            await flash("Invalid username or password.", "error")
    return await render_template("login.html")


@app.route("/logout")
@login_required
async def logout():
    session.pop("username", None)
    return redirect(url_for("login"))


# --- Main Application Logic ---
@app.before_serving
async def startup() -> None:
    """Initial setup before serving requests."""
    app.logger.info(  # Verify the event loop policy being used.
        f"{__name__.title()} is running with "
        f"{asyncio.get_event_loop().__class__.__module__}."
    )


@app.route("/")
@login_required
async def index() -> str | Response:
    """Main route for displaying and searching logs."""
    # Prepare the context for rendering the index page.
    context: Dict[str, Any] = {
        "request": request,
        "available_dbs": await get_available_databases(CFG),
        "search_query": request.args.get("q", "").strip(),
        "filters": {  # Dictionary comprehension to get filter values.
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

    # Check if the page is loaded with no specific filters.
    is_unfiltered_load = (
        not context["search_query"]
        and not context["filters"]["from_host"]
        and not context["filters"]["received_at_min"]
        and not context["filters"]["received_at_max"]
    )

    # If it's an unfiltered load, set the default time to the last hour
    # to avoid loading too many logs at once which can be slow.
    if is_unfiltered_load:
        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        # The HTML input type="datetime-local" expects 'YYYY-MM-DDTHH:MM'
        context["filters"]["received_at_min"] = one_hour_ago.strftime(
            "%Y-%m-%dT%H:%M"
        )

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

    start_time: float = time.perf_counter()  # Start measuring query time.

    query_context = QueryContext(
        db_path=selected_db,
        search_query=context["search_query"],
        filters=context["filters"],
        last_id=request.args.get("last_id", type=int),
        direction=request.args.get("direction", "next").strip(),
        page_size=50,
    )

    log_query = LogQuery(query_context, logger)
    db_results = await log_query.run()

    redacted_logs: Generator[dict[Any, str | Any], None, None] | None = None
    # If REDACT is enabled, redact sensitive information in logs.
    if REDACT and db_results["logs"]:
        # This is a generator to avoid loading all logs into memory at once.
        redacted_logs = (
            # Dictionary comprehension for redacting sensitive information
            # in the "Message" field while keeping other fields intact.
            {
                key: redact(row[key], "▒") if key == "Message" else row[key]
                for key in row.keys()
            }
            for row in db_results["logs"]
        )

    context.update(
        {
            "logs": redacted_logs or db_results["logs"],
            "total_logs": db_results["total_logs"],
            "page_info": db_results["page_info"],
            "debug_query": "\n\n---\n\n".join(db_results["debug_info"]),
            "error": db_results["error"],
            "query_time": time.perf_counter() - start_time,
        }
    )

    return await render_template("index.html", **context)


@app.route("/users")
@login_required
@admin_required
async def list_users():
    return await render_template(
        "users.html", users=auth_manager.users.values()
    )


@app.route("/users/add", methods=["GET", "POST"])
@login_required
@admin_required
async def add_user():
    if request.method == "POST":
        form = await request.form
        username = form.get("username")
        password = form.get("password")
        is_admin = form.get("is_admin") == "on"
        success, message = auth_manager.add_user(username, password, is_admin)
        if success:
            await flash(message, "success")
            return redirect(url_for("list_users"))
        else:
            await flash(message, "error")
    return await render_template("user_form.html", user=None, title="Add User")


@app.route("/users/edit/<username>", methods=["GET", "POST"])
@login_required
@admin_required
async def edit_user(username):
    user = auth_manager.get_user(username)
    if not user:
        abort(404)
    if request.method == "POST":
        form = await request.form
        new_password = form.get("password")
        is_admin = form.get("is_admin") == "on"
        is_enabled = form.get("is_enabled") == "on"

        if new_password:
            auth_manager.update_password(username, new_password)
            await flash("Password updated.", "success")

        auth_manager.set_user_admin_status(username, is_admin)
        auth_manager.set_user_enabled_status(username, is_enabled)
        await flash("User updated.", "success")

        return redirect(url_for("list_users"))
    return await render_template("user_form.html", user=user, title="Edit User")


@app.route("/users/delete/<username>", methods=["POST"])
@login_required
@admin_required
async def delete_user(username):
    if username == session.get("username"):
        await flash("You cannot delete yourself.", "error")
        return redirect(url_for("list_users"))

    success, message = auth_manager.delete_user(username)
    if success:
        await flash(message, "success")
    else:
        await flash(message, "error")
    return redirect(url_for("list_users"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
async def profile():
    username = session["username"]
    if request.method == "POST":
        form = await request.form
        new_password = form.get("password")
        if new_password:
            success, message = auth_manager.update_password(
                username, new_password
            )
            if success:
                await flash(message, "success")
            else:
                await flash(message, "error")
        return redirect(url_for("profile"))
    return await render_template("profile.html")


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

    parser = argparse.ArgumentParser(description="aiosyslogd web interface.")
    parser.add_argument(
        "--quart",
        action="store_true",
        help="Force the use of Quart's built-in server.",
    )
    args = parser.parse_args()

    host: str = WEB_SERVER_CFG.get("bind_ip", "127.0.0.1")
    port: int = WEB_SERVER_CFG.get("bind_port", 5141)
    logger.info(f"Starting aiosyslogd-web interface on http://{host}:{port}")

    use_uvicorn = False
    if not args.quart:
        try:
            import uvicorn  # type: ignore

            use_uvicorn = True
        except ImportError:
            logger.warning(
                "Uvicorn is not installed. Falling back to Quart's server."
            )
            logger.warning(
                "For production, it is recommended to install uvicorn:"
            )
            logger.warning("poetry install -E prod")

    if use_uvicorn:
        uvicorn.run(app, host=host, port=port)
    else:
        # Install uvloop if available for better performance.
        if uvloop:
            uvloop.install()
        app.run(host=host, port=port, debug=DEBUG)


if __name__ == "__main__":
    main()
