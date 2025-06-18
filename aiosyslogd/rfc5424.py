# -*- coding: utf-8 -*-
from datetime import datetime, UTC
from typing import Dict
import re
from loguru import logger

# --- RFC 5424 and RFC 3164 Syslog Message Patterns ---
# These patterns are compiled here to avoid repeated compilation.

# Pattern for RFC 5424: <PRI>VER TS HOST APP PID MSGID SD MSG
RFC5424_PATTERN: re.Pattern[str] = re.compile(
    r"<(?P<pri>\d+)>"
    r"(?P<ver>\d+)\s"
    r"(?P<ts>\S+)\s"
    r"(?P<host>\S+)\s"
    r"(?P<app>\S+)\s"
    r"(?P<pid>\S+)\s"
    r"(?P<msgid>\S+)\s"
    r"(?P<sd>(\-|(?:\[.+?\])+))\s?"
    r"(?P<msg>.*)",
    re.DOTALL,
)

# Pattern for RFC 3164: <PRI>MMM DD HH:MM:SS HOSTNAME TAG[PID]: MSG
# Made the colon after the tag optional and adjusted tag capture.
RFC3164_PATTERN: re.Pattern[str] = re.compile(
    r"<(?P<pri>\d{1,3})>"
    r"(?P<mon>\w{3})\s+(?P<day>\d{1,2})\s+(?P<hr>\d{2}):(?P<min>\d{2}):(?P<sec>\d{2})"
    r"\s+(?P<host>[\w\-\.]+)"
    r"\s+(?P<tag>\S+?)(:|\s-)?\s"  # Flexible tag/separator matching
    r"(?P<msg>.*)",
    re.DOTALL,
)


def convert_rfc3164_to_rfc5424(message: str, debug_mode: bool = False) -> str:
    """
    Converts a best-effort RFC 3164 syslog message to an RFC 5424 message.
    This version is more flexible to handle formats like FortiGate's.
    """
    match: re.Match[str] | None = RFC3164_PATTERN.match(message)

    if not match:
        if debug_mode:
            logger.debug(
                f"Not an RFC 3164 message, returning original: {message}"
            )
        return message

    parts: Dict[str, str] = match.groupdict()
    priority: str = parts["pri"]
    hostname: str = parts["host"]
    raw_tag: str = parts["tag"]
    msg: str = parts["msg"].strip()

    app_name: str = raw_tag
    procid: str = "-"
    pid_match: re.Match[str] | None = re.match(r"^(.*)\[(\d+)\]$", raw_tag)
    if pid_match:
        app_name = pid_match.group(1)
        procid = pid_match.group(2)

    try:
        now: datetime = datetime.now()
        # Add the current year to the string and the format to avoid the Python 3.13+ DeprecationWarning
        date_string_with_year: str = (
            f"{now.year} {parts['mon']} {parts['day']} {parts['hr']}:{parts['min']}:{parts['sec']}"
        )
        dt_naive: datetime = datetime.strptime(
            date_string_with_year,
            "%Y %b %d %H:%M:%S",
        )

        # If the parsed date is in the future, it must be from the previous year.
        # This handles cases like a log from December being processed in January.
        if dt_naive > now:
            dt_naive = dt_naive.replace(year=now.year - 1)

        dt_aware: datetime = dt_naive.astimezone().astimezone(UTC)
        timestamp: str = dt_aware.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except ValueError:
        if debug_mode:
            logger.debug(
                "Could not parse RFC-3164 timestamp, using current time."
            )
        timestamp = (
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )

    return f"<{priority}>1 {timestamp} {hostname} {app_name} {procid} - - {msg}"


def normalize_to_rfc5424(message: str, debug_mode: bool = False) -> str:
    """
    Ensures a syslog message is in RFC 5424 format.
    Converts RFC 3164 messages, and leaves RFC 5424 as is.
    """
    pri_end: int = message.find(">")
    if pri_end > 0 and len(message) > pri_end + 2:
        # A valid RFC5424 message has a version '1' right after the priority tag
        if message[pri_end + 1] == "1" and message[pri_end + 2].isspace():
            return message

    return convert_rfc3164_to_rfc5424(message, debug_mode)
