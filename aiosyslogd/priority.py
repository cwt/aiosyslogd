# -*- coding: utf-8 -*-
from typing import Tuple, Dict


class SyslogMatrix:
    """A class to decode syslog priority codes."""

    LEVELS: Tuple[str, ...] = (
        "emergency",
        "alert",
        "critical",
        "error",
        "warning",
        "notice",
        "info",
        "debug",
    )
    FACILITIES: Tuple[str, ...] = (
        "kernel",
        "user",
        "mail",
        "system",
        "security0",
        "syslog",
        "lpd",
        "nntp",
        "uucp",
        "time",
        "security1",
        "ftpd",
        "ntpd",
        "logaudit",
        "logalert",
        "clock",
        "local0",
        "local1",
        "local2",
        "local3",
        "local4",
        "local5",
        "local6",
        "local7",
    )

    def __init__(self) -> None:
        """Initializes the SyslogMatrix with a mapping of priority codes."""
        self.matrix: Dict[str, Tuple[str, str]] = {}
        i: int = 0
        for facility in self.FACILITIES:
            for level in self.LEVELS:
                self.matrix[str(i)] = (facility, level)
                i += 1

    def decode(
        self, code: str | int
    ) -> Tuple[Tuple[str, int], Tuple[str, int]]:
        """Decodes a syslog priority code into facility and level tuples."""
        str_code: str = str(code)
        facility_str, level_str = self.matrix.get(
            str_code, ("kernel", "emergency")
        )  # Fallback to 0, 0
        return (
            (facility_str, self.FACILITIES.index(facility_str)),
            (level_str, self.LEVELS.index(level_str)),
        )

    def decode_int(self, code: str | int) -> Tuple[int, int]:
        """Decodes a syslog priority code into facility and level integer indices."""
        facility, level = self.decode(code)
        return (facility[1], level[1])
