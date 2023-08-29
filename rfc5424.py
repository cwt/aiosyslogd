import re
from datetime import datetime
from itertools import zip_longest


def convert_rfc5424_to_rfc3164(message):
    # Extract the required fields from the RFC-5424 message
    # Example RFC-5424 message: '<189>Jun 20 06:31:32 FortiGate-101F_02 - eventtime=1687242692932574340 ...
    pattern = r"<(\d+)>(\d+ ){0,1}(\S+ \d+ \d+:\d+:\d+) ([\S\s]+) - ([\S\s]+)"
    match = re.match(pattern, message)

    if not match:
        return message

    # Extract the necessary fields
    priority = match.group(1)
    version = match.group(2)
    timestamp = match.group(3)
    hostname = match.group(4)  # Hostname with optional appname and procid
    message = match.group(5)

    # Convert the priority to RFC-3164 format
    rfc5424_pri = int(priority)
    facility = rfc5424_pri // 8
    severity = rfc5424_pri % 8
    rfc3164_pri = facility * 8 + severity

    # Convert the timestamp to RFC-3164 format (MMM DD HH:MM:SS)
    timestamp_dt = datetime.strptime(timestamp, "%b %d %H:%M:%S")
    timestamp_rfc3164 = timestamp_dt.strftime("%b %d %H:%M:%S")

    # Rearrange the fields according to RFC-3164 format
    rfc3164_message = "<{}>{} {}: {}".format(
        rfc3164_pri, timestamp_rfc3164, hostname, message
    )

    return rfc3164_message
