#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
loggen.py - A simple syslog message generator for testing.

This script generates a specified number of fake syslog messages and sends them
via UDP to a given host and port. It's useful for load-testing syslog servers
like rsyslog, syslog-ng, or the provided aiosyslogd.

Features:
- Generates messages in a format similar to RFC 3164.
- Allows customization of the number of messages to send.
- Can target any host and port.
- Includes a variety of sample messages and application names.

Usage:
    python loggen.py -n 1000
    python loggen.py --num-messages 500 --host 127.0.0.1 --port 5140
"""

import socket
import argparse
import time
import random
from datetime import datetime

# --- Configuration ---
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5140
DEFAULT_NUM_MESSAGES = 100

# --- Sample Data for Log Generation ---
FACILITIES = {
    "kern": 0, "user": 1, "mail": 2, "daemon": 3,
    "auth": 4, "syslog": 5, "lpr": 6, "news": 7,
    "uucp": 8, "cron": 9, "authpriv": 10, "ftp": 11,
    "local0": 16, "local1": 17, "local2": 18, "local3": 19,
    "local4": 20, "local5": 21, "local6": 22, "local7": 23,
}

SEVERITIES = {
    "emerg": 0, "alert": 1, "crit": 2, "err": 3,
    "warn": 4, "notice": 5, "info": 6, "debug": 7,
}

APP_NAMES = [
    "sshd", "kernel", "CRON", "systemd", "web-server",
    "db-backup", "firewall", "app-login"
]

MESSAGES = [
    "User '{user}' logged in from {ip_addr}",
    "Failed password for {user} from {ip_addr} port {port} ssh2",
    "session opened for user {user} by (uid=0)",
    "session closed for user {user}",
    "Connection from {ip_addr} port {port}",
    "Received disconnect from {ip_addr} port {port}:11: disconnected by user",
    "Invalid user {user} from {ip_addr}",
    "kernel: microcode: updated early: 0x{hex_val} -> 0x{hex_val}",
    "pam_unix(sshd:session): session opened for user {user}",
    "error: {error_type} on device {device_id}",
]

USERS = ["root", "admin", "testuser", "deploy", "guest", "service-acc"]

ERROR_TYPES = ["read error", "write error", "connection timeout", "permission denied"]

def generate_log_message() -> bytes:
    """
    Generates a single, pseudo-random syslog message in RFC 3164 format.
    """
    facility_name = random.choice(list(FACILITIES.keys()))
    severity_name = random.choice(list(SEVERITIES.keys()))
    facility_code = FACILITIES[facility_name]
    severity_code = SEVERITIES[severity_name]

    # Calculate priority value
    priority = (facility_code * 8) + severity_code

    # Get current time and format it
    timestamp = datetime.now().strftime("%b %d %H:%M:%S")

    hostname = socket.gethostname()
    app_name = random.choice(APP_NAMES)
    pid = random.randint(1000, 9999)

    # Create a dynamic message
    message_template = random.choice(MESSAGES)
    message = message_template.format(
        user=random.choice(USERS),
        ip_addr=f"{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}",
        port=random.randint(1024, 65535),
        hex_val=''.join(random.choices('0123456789abcdef', k=8)),
        error_type=random.choice(ERROR_TYPES),
        device_id=f"sd{random.choice('abc')}"
    )

    # Assemble the final log message
    log_line = f"<{priority}>{timestamp} {hostname} {app_name}[{pid}]: {message}"
    return log_line.encode("utf-8")

def main():
    """Main function to parse arguments and send logs."""
    parser = argparse.ArgumentParser(
        description="Syslog message generator for testing purposes.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-n", "--num-messages",
        type=int,
        default=DEFAULT_NUM_MESSAGES,
        help=f"Number of log messages to generate.\n(default: {DEFAULT_NUM_MESSAGES})"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_HOST,
        help=f"The target host to send logs to.\n(default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"The target UDP port.\n(default: {DEFAULT_PORT})"
    )
    args = parser.parse_args()

    host = args.host
    port = args.port
    num_messages = args.num_messages

    print(f"Preparing to send {num_messages} log messages to {host}:{port} via UDP...")

    try:
        # Create a UDP socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            start_time = time.time()
            for i in range(num_messages):
                log_message = generate_log_message()
                sock.sendto(log_message, (host, port))
                # Optional: print progress to the console
                if (i + 1) % 100 == 0:
                    print(f"Sent {i + 1}/{num_messages} messages...", end='\r')
            
            # Ensure the final count is printed
            print(f"Sent {num_messages}/{num_messages} messages.      ")

            end_time = time.time()
            duration = end_time - start_time
            rate = num_messages / duration if duration > 0 else float('inf')

            print("\n--- Summary ---")
            print(f"Total messages sent: {num_messages}")
            print(f"Time taken: {duration:.2f} seconds")
            print(f"Average rate: {rate:.2f} messages/sec")
            print("-----------------")

    except socket.gaierror:
        print(f"\nError: Hostname '{host}' could not be resolved. Please check the hostname and your network connection.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    main()

