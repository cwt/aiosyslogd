from datetime import datetime
from unittest.mock import patch
import pytest
import re

# --- Import the classes and functions to be tested ---
from aiosyslogd.priority import SyslogMatrix
from aiosyslogd.rfc5424 import normalize_to_rfc5424, convert_rfc3164_to_rfc5424

# --- Test Suite for priority.py ---


class TestSyslogMatrix:
    """Tests for the SyslogMatrix priority decoder."""

    @pytest.fixture(scope="class")
    def matrix(self):
        """Provide a single SyslogMatrix instance for all tests in this class."""
        return SyslogMatrix()

    def test_decode_kernel_emergency(self, matrix):
        """Tests decoding of priority 0 (kernel.emergency)."""
        facility, level = matrix.decode(0)
        assert facility == ("kernel", 0)
        assert level == ("emergency", 0)

    def test_decode_user_notice(self, matrix):
        """Tests decoding of priority 13 (user.notice)."""
        facility, level = matrix.decode(13)
        assert facility == ("user", 1)
        assert level == ("notice", 5)

    def test_decode_local7_debug(self, matrix):
        """Tests decoding of the highest priority 191 (local7.debug)."""
        facility, level = matrix.decode(191)
        assert facility == ("local7", 23)
        assert level == ("debug", 7)

    def test_decode_int(self, matrix):
        """Tests the decode_int method for direct integer output."""
        facility_int, level_int = matrix.decode_int(13)
        assert facility_int == 1
        assert level_int == 5

    def test_decode_invalid_code_fallback(self, matrix):
        """Tests that an invalid code falls back to kernel.emergency."""
        facility, level = matrix.decode(999)
        assert facility == ("kernel", 0)
        assert level == ("emergency", 0)


# --- Test Suite for rfc5424.py ---


class TestRfc5424Conversion:
    """Tests for syslog message format normalization."""

    def test_normalize_already_rfc5424(self):
        """Tests that a message already in RFC5424 format is not changed."""
        rfc5424_msg = "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su - ID47 - 'su root' failed for lonvick on /dev/pts/8"
        normalized = normalize_to_rfc5424(rfc5424_msg)
        assert normalized == rfc5424_msg

    def test_normalize_standard_rfc3164(self):
        """Tests conversion of a standard RFC3164 message."""
        rfc3164_msg = "<34>Oct 11 22:14:15 mymachine su: 'su root' failed for lonvick on /dev/pts/8"

        # We can't test the timestamp exactly, so we check the structure.
        normalized = normalize_to_rfc5424(rfc3164_msg)

        assert normalized.startswith("<34>1 ")
        assert "mymachine" in normalized
        assert "su" in normalized  # app-name
        assert "- - " in normalized  # msgid and sd
        assert normalized.endswith("'su root' failed for lonvick on /dev/pts/8")

    def test_normalize_rfc3164_with_pid(self):
        """Tests conversion of an RFC3164 message that includes a PID."""
        rfc3164_msg = (
            "<13>Feb  5 10:01:02 host CRON[12345]: (root) CMD (command)"
        )

        normalized = normalize_to_rfc5424(rfc3164_msg)

        assert normalized.startswith("<13>1 ")
        assert " host " in normalized
        assert " CRON " in normalized
        assert " 12345 " in normalized  # procid
        assert normalized.endswith("(root) CMD (command)")

    def test_normalize_unparseable_message(self):
        """Tests that a message that doesn't match either format is returned as-is."""
        plain_msg = "this is just a plain log message"
        normalized = normalize_to_rfc5424(plain_msg)
        assert normalized == plain_msg

    @patch("aiosyslogd.rfc5424.datetime")
    def test_rfc3164_timestamp_conversion_past(self, mock_datetime):
        """
        Tests that a timestamp from a previous month is correctly assigned to the previous year.
        """
        # Simulate that "now" is Jan 2025
        mock_datetime.now.return_value = datetime(2025, 1, 15)

        # Configure the mock to use the real strptime method so it returns a real datetime object
        mock_datetime.strptime = datetime.strptime

        # This log is from December, so it should be from 2024
        rfc3164_msg = "<34>Dec 10 22:14:15 mymachine su: test"

        normalized = convert_rfc3164_to_rfc5424(rfc3164_msg)

        # Check that the year in the timestamp is correct
        assert "2024-12-10T" in normalized


def test_normalize_to_rfc5424_debug_mode(capsys):
    """Tests that a debug message is printed when normalizing a non-syslog message in debug mode."""
    message = "this is not a syslog message"
    debug_mode = True

    # Normalize the message
    normalized = normalize_to_rfc5424(message, debug_mode)

    # Capture console output and verify debug message
    captured = capsys.readouterr()
    assert (
        "[RFC-CONVERT] Not an RFC 3164 message, returning original: this is not a syslog message"
        in captured.out
    )
    # Ensure the message is returned unchanged
    assert normalized == message


def test_convert_rfc3164_to_rfc5424_timestamp_error(capsys):
    """Tests that a debug message is printed when an RFC3164 timestamp cannot be parsed in debug mode."""
    message = "<34>Feb 30 22:14:15 mymachine su: test"  # Invalid date (Feb 30)
    debug_mode = True

    # Convert the message
    normalized = convert_rfc3164_to_rfc5424(message, debug_mode)

    # Capture console output and verify debug message
    captured = capsys.readouterr()
    assert (
        "[RFC-CONVERT] Could not parse RFC-3164 timestamp, using current time."
        in captured.out
    )
    # Verify the output is RFC5424 with a current timestamp
    parts = normalized.split()
    assert parts[0] == "<34>1"
    assert re.match(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", parts[1]
    )  # ISO timestamp
    assert parts[2] == "mymachine"
    assert parts[-1] == "test"
