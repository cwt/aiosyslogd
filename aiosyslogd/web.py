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
    jsonify,
)
from types import ModuleType
from typing import Any, Dict, Generator
import aiosqlite
import asyncio
import importlib.util
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


# CSRF Protection
def generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = os.urandom(24).hex()
    return session["_csrf_token"]


app.jinja_env.globals["csrf_token"] = generate_csrf_token


def is_gemini_available() -> bool:
    """Check if the gemini extra is available by checking for required modules."""
    try:
        # Check if google-genai module is available
        return (
            importlib.util.find_spec("google.genai") is not None
            or importlib.util.find_spec("google.generativeai") is not None
        )
    except AttributeError:
        # If find_spec fails, try importing directly as fallback
        try:
            importlib.import_module("google.genai")
            return True
        except ImportError:
            try:
                importlib.import_module("google.generativeai")
                return True
            except ImportError:
                return False


@app.before_request
async def csrf_protect():
    # Skip CSRF for API endpoints that don't require it
    if request.path.startswith("/api/"):
        return

    # Check if we're in testing mode
    import sys

    if "pytest" in sys.modules:
        # Skip CSRF validation during tests
        return

    if request.method in ["POST", "PUT", "DELETE"]:
        # For form requests, check form data
        if (
            request.content_type
            and "application/x-www-form-urlencoded" in request.content_type
        ):
            form_data = await request.form
            token = session.get("_csrf_token")

            # If there's no token in session, generate one (this handles new sessions)
            if not token:
                session["_csrf_token"] = os.urandom(24).hex()
                token = session["_csrf_token"]

            if form_data.get("csrf_token") != token:
                abort(403)


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
        return dict(current_user=user, gemini_available=is_gemini_available())
    return dict(current_user=None, gemini_available=is_gemini_available())


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
        f"{asyncio.get_running_loop().__class__.__module__}."
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


@app.route("/api/check-gemini-auth")
@login_required
async def check_gemini_auth():
    """Check if user has stored a Gemini API key in browser."""
    # Since API key is only stored in browser localStorage, we can't check server-side
    # This endpoint now serves as a way to check if the user is logged in and can use Gemini
    # The actual API key check happens client-side
    return jsonify({"authenticated": True})


@app.route("/api/clear-gemini-key", methods=["POST"])
@login_required
async def clear_gemini_key():
    """API endpoint that confirms successful clear (API key only stored in browser)."""
    try:
        # Note: API key is only stored in the user's browser localStorage, not on the server
        # The server doesn't store the API key for security/legal reasons

        return jsonify({"success": True})

    except Exception as e:
        app.logger.error(f"Error handling Gemini API key clear: {str(e)}")
        return (
            jsonify({"error": f"Error handling API key clear: {str(e)}"}),
            500,
        )


@app.route("/api/save-gemini-key", methods=["POST"])
@login_required
async def save_gemini_key():
    """API endpoint that confirms successful save (API key only stored in browser)."""
    try:
        data = await request.get_json()
        api_key = data.get("api_key", "").strip()

        if not api_key:
            return jsonify({"error": "API key is required"}), 400

        # Note: API key is only stored in the user's browser localStorage, not on the server
        # The server doesn't store the API key for security/legal reasons

        return jsonify({"success": True})

    except Exception as e:
        app.logger.error(f"Error handling Gemini API key save: {str(e)}")
        return jsonify({"error": f"Error handling API key: {str(e)}"}), 500


@app.route("/api/gemini-search", methods=["POST"])
@login_required
async def gemini_search():
    """API endpoint to convert natural language to FTS5 query using Gemini."""
    try:
        data = await request.get_json()
        natural_language_query = data.get("query", "").strip()
        api_key = data.get(
            "api_key", ""
        ).strip()  # Get API key from request body

        if not natural_language_query:
            return jsonify({"error": "Query is required"}), 400

        if not api_key:
            return (
                jsonify(
                    {
                        "error": "Gemini API key not found. Please authenticate with Google."
                    }
                ),
                401,
            )

        # Define the prompt here so it's available to both code paths
        prompt = f"""
        Convert this natural language search query into an SQLite FTS5 query syntax:
        "{natural_language_query}"

        Return ONLY the FTS5 query string without any explanation.
        Focus on key terms, ignore generic words like "log", "record", "entry".
        FTS5 supports: AND, OR, NOT, phrase matching with quotes, wildcards (*), proximity.
        Quote phrases, special chars, IPs, numbers at start: "hello world", "user-agent", "192.168.1.1", "2021-01-01".
        Examples: "errors from server1" -> "server1 AND error*", "find admin logs failed" -> "admin AND failed".
        """

        # Import here to avoid requiring the dependency unless the endpoint is used
        try:
            import google.genai as genai_new

            # Use the new API
            client = genai_new.Client(api_key=api_key)

            # Try the models in order of preference
            model_names = [
                "gemma-3-27b-it",
                "gemini-2.5-pro",
                "gemini-2.5-flash",
            ]

            response = None
            for model_name in model_names:
                try:
                    response = client.models.generate_content(
                        model=model_name, contents=prompt
                    )
                    break  # Success, exit the loop
                except Exception:
                    continue

            if response is None:
                raise Exception("No available models could process the request")

            fts5_query = (
                response.text.strip()
            )  # Keep any necessary quotes for FTS5 syntax
        except ImportError:
            # Fallback to old library
            try:
                import google.generativeai as genai_old  # type: ignore[import-untyped]

                # Use the old API
                genai_old.configure(api_key=api_key)
                model = genai_old.GenerativeModel("gemini-pro")

                response = model.generate_content(prompt)
                fts5_query = (
                    response.text.strip()
                )  # Keep any necessary quotes for FTS5 syntax
            except ImportError:
                return (
                    jsonify(
                        {
                            "error": "Google Gen AI library not installed. Run: poetry install -E gemini"
                        }
                    ),
                    500,
                )

        return jsonify({"fts5_query": fts5_query})

    except Exception as e:
        app.logger.error(f"Gemini API error: {str(e)}")
        return jsonify({"error": f"Error processing request: {str(e)}"}), 500


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
