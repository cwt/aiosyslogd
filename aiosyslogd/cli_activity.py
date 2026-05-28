#!/usr/bin/env python
"""CLI for user activity analysis — shares the same engine as the web UI."""

from __future__ import annotations

import argparse
import asyncio
import sys

from .activity import run_activity_report
from .activity.parsers import get_activity_parser


def _make_table(users):  # noqa: F821  # type: ignore[no-untyped-def]
    from prettytable import PrettyTable
    from prettytable import HRuleStyle

    table = PrettyTable()
    table.field_names = ["User", "Category", "App Breakdown", "Total"]
    table.align["User"] = "l"
    table.align["Category"] = "l"
    table.align["App Breakdown"] = "l"
    table.align["Total"] = "r"
    table.hrules = HRuleStyle.ALL

    for u in users:
        user_lines = []
        category_lines = []
        app_lines = []
        total_lines = []

        user_lines.append(u.user)
        total_lines.append(f"{u.total_minutes}m")

        for idx, g in enumerate(u.appcats):
            if idx > 0:
                # Add spacing between categories
                category_lines.append("")
                app_lines.append("")
                user_lines.append("")
                total_lines.append("")

            category_lines.append(f"{g.total_minutes:>4}m  {g.appcat}")

            for app_idx, a in enumerate(g.apps):
                if app_idx > 0:
                    category_lines.append("")
                    user_lines.append("")
                    total_lines.append("")
                app_lines.append(f"{a.minutes:>4}m  {a.app}")

        max_lines = len(app_lines)
        while len(user_lines) < max_lines:
            user_lines.append("")
        while len(total_lines) < max_lines:
            total_lines.append("")

        table.add_row(
            [
                "\n".join(user_lines),
                "\n".join(category_lines),
                "\n".join(app_lines),
                "\n".join(total_lines),
            ]
        )
    return table


def _render_report(report) -> str:
    import shutil

    lines = []
    total = report.total_logs
    qtime = report.query_time
    window = report.total_window_minutes

    lines.append(
        f"{total:,} matching logs in {qtime:.3f}s" f"  |  window: {window} min"
    )
    lines.append("")

    if not report.users:
        lines.append("No user activity found.")
        return "\n".join(lines)

    # First, make the full table to measure its width
    full_table = _make_table(report.users)
    full_str = full_table.get_string()
    table_width = len(full_str.splitlines()[0]) if full_str else 0

    term_width, _ = shutil.get_terminal_size()
    gap = 4

    if len(report.users) > 1 and (2 * table_width + gap) <= term_width:
        half = (len(report.users) + 1) // 2
        left_users = report.users[:half]
        right_users = report.users[half:]

        left_table = _make_table(left_users)
        right_table = _make_table(right_users)

        left_lines = left_table.get_string().splitlines()
        right_lines = right_table.get_string().splitlines()

        left_w = len(left_lines[0]) if left_lines else 0
        max_len = max(len(left_lines), len(right_lines))

        table_lines = []
        for i in range(max_len):
            left_part = left_lines[i] if i < len(left_lines) else " " * left_w
            right_part = right_lines[i] if i < len(right_lines) else ""
            table_lines.append(f"{left_part}{' ' * gap}{right_part}")

        lines.extend(table_lines)
    else:
        lines.append(full_str)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze per-user per-app activity from syslog database",
        epilog="Example: aiosyslogd-activity -d syslog_202604.sqlite3 "
        "-q '\"srcip 10 100 141\" AND (youtube OR facebook)' "
        '--from "2026-04-27 15:00" --to "2026-04-27 16:00"',
    )
    parser.add_argument(
        "-d",
        "--db",
        required=True,
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "-q",
        "--query",
        required=True,
        help="FTS5 search query for Message column",
    )
    parser.add_argument(
        "--from",
        dest="from_time",
        default="",
        help="Start of time range (YYYY-MM-DD [HH:MM])",
    )
    parser.add_argument(
        "--to",
        dest="to_time",
        default="",
        help="End of time range (YYYY-MM-DD [HH:MM])",
    )
    parser.add_argument(
        "--parser",
        dest="parser_name",
        default=None,
        help="Activity parser to use (default: from config [activity.parser] or 'fortios')",
    )
    args = parser.parse_args()

    parser_name = args.parser_name
    if parser_name is None:
        try:
            from .config import load_config

            cfg = load_config()
            parser_name = cfg.get("activity", {}).get("parser", "fortios")
        except Exception:
            parser_name = "fortios"

    filters = {
        "from_host": "",
        "received_at_min": args.from_time,
        "received_at_max": args.to_time,
    }

    report = asyncio.run(
        run_activity_report(
            db_path=args.db,
            search_query=args.query,
            filters=filters,
            parser=get_activity_parser(parser_name),
        )
    )

    if report.error:
        print(f"Error: {report.error}", file=sys.stderr)
        sys.exit(1)

    print(_render_report(report))


if __name__ == "__main__":
    main()
