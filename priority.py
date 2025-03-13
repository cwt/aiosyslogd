# -*- coding: utf-8 -*-


class SyslogMatrix:
    LEVELS = (
        "emergency",
        "alert",
        "critical",
        "error",
        "warning",
        "notice",
        "info",
        "debug",
    )
    FACILITIES = (
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

    def __init__(self):
        self.matrix = {}
        i = 0
        for facility in self.FACILITIES:
            for level in self.LEVELS:
                self.matrix[str(i)] = (facility, level)
                i += 1

    def decode(self, code):
        code = str(code) if isinstance(code, int) else code
        facility, level = self.matrix.get(
            code, ("kernel", "emergency")
        )  # Fallback to 0, 0
        return (
            (facility, self.FACILITIES.index(facility)),
            (level, self.LEVELS.index(level)),
        )

    def decode_int(self, code):
        facility, level = self.decode(code)
        return (facility[1], level[1])
