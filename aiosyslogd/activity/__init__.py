import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import aiosqlite
import sqlite3

from aiosyslogd.activity.parsers import BaseActivityParser, FortiOSParser
from aiosyslogd.db.sqlite_utils import get_time_boundary_ids


@dataclass
class AppActivity:
    app: str
    minutes: int


@dataclass
class AppcatGroup:
    appcat: str
    apps: List[AppActivity] = field(default_factory=list)
    total_minutes: int = 0


@dataclass
class ActivityResult:
    user: str
    appcats: List[AppcatGroup] = field(default_factory=list)
    total_minutes: int = 0


@dataclass
class ActivityReport:
    timeframe: Dict[str, str]
    total_window_minutes: int
    users: List[ActivityResult]
    total_logs: int
    query_time: float
    error: Optional[str] = None


def _parse_time_string(time_str: str) -> datetime:
    time_str = time_str.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse time string: {time_str}")


async def run_activity_report(
    db_path: str,
    search_query: str,
    filters: Dict[str, str],
    parser: Optional[BaseActivityParser] = None,
) -> ActivityReport:
    start_time = time.perf_counter()
    if parser is None:
        parser = FortiOSParser()

    try:
        db_uri = f"file:{db_path}?mode=ro"
        async with aiosqlite.connect(
            db_uri,
            uri=True,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        ) as conn:
            conn.row_factory = aiosqlite.Row

            min_filter = filters.get("received_at_min", "")
            max_filter = filters.get("received_at_max", "")

            if min_filter:
                try:
                    start_dt = _parse_time_string(min_filter)
                except ValueError:
                    start_dt = None
            else:
                start_dt = None

            if max_filter:
                try:
                    end_dt = _parse_time_string(max_filter)
                except ValueError:
                    end_dt = None
            else:
                end_dt = None

            if start_dt is None:
                async with conn.execute(
                    "SELECT MIN(ReceivedAt) FROM SystemEvents"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0]:
                        val = row[0]
                        try:
                            start_dt = (
                                val
                                if isinstance(val, datetime)
                                else _parse_time_string(str(val))
                            )
                        except ValueError:
                            pass
                if start_dt is None:
                    start_dt = datetime.now() - timedelta(hours=1)

            if end_dt is None:
                async with conn.execute(
                    "SELECT MAX(ReceivedAt) FROM SystemEvents"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0]:
                        val = row[0]
                        try:
                            end_dt = (
                                val
                                if isinstance(val, datetime)
                                else _parse_time_string(str(val))
                            )
                        except ValueError:
                            pass
                if end_dt is None:
                    end_dt = datetime.now()

            if start_dt > end_dt:
                start_dt, end_dt = end_dt, start_dt

            fts5_query = (
                f'({search_query}) AND "user"' if search_query else '"user"'
            )

            active_minutes: Dict[Tuple[str, str, str], Set[str]] = defaultdict(
                set
            )
            total_logs = 0

            chunk_delta = timedelta(hours=1)
            current_dt = start_dt
            time_fmt = "%Y-%m-%d %H:%M:%S"

            while current_dt < end_dt:
                next_dt = min(current_dt + chunk_delta, end_dt)

                min_ts = current_dt.strftime(time_fmt)
                max_ts = next_dt.strftime(time_fmt)

                start_id, end_id, _ = await get_time_boundary_ids(
                    conn, min_ts, max_ts
                )

                if start_id is None or end_id is None:
                    current_dt = next_dt
                    continue

                where_clauses = ["ID >= ?", "ID <= ?"]
                params: list = [start_id, end_id]

                if filters.get("from_host"):
                    where_clauses.append("FromHost = ?")
                    params.append(filters["from_host"])

                if fts5_query:
                    fts_subquery = (
                        "SELECT rowid FROM SystemEvents_FTS "
                        "WHERE Message MATCH ? AND rowid >= ? AND rowid <= ?"
                    )
                    where_clauses.append(f"ID IN ({fts_subquery})")
                    params.extend([fts5_query, start_id, end_id])

                sql = (
                    "SELECT Message, ReceivedAt FROM SystemEvents WHERE "
                    + " AND ".join(where_clauses)
                )

                async with conn.execute(sql, tuple(params)) as cursor:
                    rows = list(await cursor.fetchall())
                    total_logs += len(rows)

                    for row in rows:
                        msg = row["Message"]
                        if not msg:
                            continue

                        parsed = parser.extract(msg)
                        if parsed is None:
                            continue
                        app = parsed.app
                        user = parsed.user
                        appcat = parsed.appcat

                        received_at = row["ReceivedAt"]
                        if received_at is None:
                            continue
                        if isinstance(received_at, datetime):
                            minute_bucket = received_at.strftime(
                                "%Y-%m-%d %H:%M"
                            )
                        else:
                            minute_bucket = str(received_at)[:16]

                        key = (user, appcat, app)
                        active_minutes[key].add(minute_bucket)

                current_dt = next_dt
    except (aiosqlite.Error, sqlite3.Error) as e:
        return ActivityReport(
            timeframe={
                "from": filters.get("received_at_min", ""),
                "to": filters.get("received_at_max", ""),
            },
            total_window_minutes=0,
            users=[],
            total_logs=0,
            query_time=time.perf_counter() - start_time,
            error=str(e),
        )

    window_mins = max(1, int((end_dt - start_dt).total_seconds() / 60))

    def make_inner_dict():
        return defaultdict(int)

    def make_mid_dict():
        return defaultdict(make_inner_dict)

    per_user: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(make_mid_dict)
    for (user, appcat, app), minutes in active_minutes.items():
        per_user[user][appcat][app] = len(minutes)

    def sort_by_minutes_desc(x):
        return -x[1]

    def get_total_minutes(group_or_result):
        return group_or_result.total_minutes

    users: List[ActivityResult] = []
    for user in sorted(per_user):
        appcats: List[AppcatGroup] = []
        user_total = 0
        for appcat in sorted(per_user[user]):
            apps_data = per_user[user][appcat]
            app_entries = [
                AppActivity(app=app, minutes=mins)
                for app, mins in sorted(
                    apps_data.items(), key=sort_by_minutes_desc
                )
            ]
            cat_total = sum(a.minutes for a in app_entries)
            appcats.append(
                AppcatGroup(
                    appcat=appcat,
                    apps=app_entries,
                    total_minutes=cat_total,
                )
            )
            user_total += cat_total

        appcats.sort(key=get_total_minutes, reverse=True)
        users.append(
            ActivityResult(user=user, appcats=appcats, total_minutes=user_total)
        )

    users.sort(key=get_total_minutes, reverse=True)

    report = ActivityReport(
        timeframe={"from": min_filter, "to": max_filter},
        total_window_minutes=window_mins,
        users=users,
        total_logs=total_logs,
        query_time=time.perf_counter() - start_time,
    )
    return report
