from datetime import datetime
from loguru import logger
from unittest.mock import patch
import pytest
import re
import sys

# --- Import the classes and functions to be tested ---
from aiosyslogd.priority import SyslogMatrix
from aiosyslogd.rfc5424 import normalize_to_rfc5424, convert_rfc3164_to_rfc5424


@pytest.fixture(autouse=True)
def setup_logger(capsys):
    """Ensure logger is configured to output to stderr for capture."""
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    yield
    logger.remove()


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


class TestRfc5424Conversion:
    """Tests for syslog message format normalization."""

    def test_normalize_already_rfc5424(self):
        rfc5424_msg = "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su - ID47 - 'su root' failed for lonvick on /dev/pts/8"
        normalized = normalize_to_rfc5424(rfc5424_msg)
        assert normalized == rfc5424_msg

    def test_normalize_standard_rfc3164(self):
        rfc3164_msg = "<34>Oct 11 22:14:15 mymachine su: 'su root' failed for lonvick on /dev/pts/8"
        normalized = normalize_to_rfc5424(rfc3164_msg)
        assert normalized.startswith("<34>1 ")
        assert "mymachine" in normalized
        assert "su" in normalized
        assert "- - " in normalized
        assert normalized.endswith("'su root' failed for lonvick on /dev/pts/8")

    def test_normalize_rfc3164_with_pid(self):
        rfc3164_msg = (
            "<13>Feb  5 10:01:02 host CRON[12345]: (root) CMD (command)"
        )
        normalized = normalize_to_rfc5424(rfc3164_msg)
        assert normalized.startswith("<13>1 ")
        assert " host " in normalized
        assert " CRON " in normalized
        assert " 12345 " in normalized
        assert normalized.endswith("(root) CMD (command)")

    def test_normalize_unparseable_message(self):
        plain_msg = "this is just a plain log message"
        normalized = normalize_to_rfc5424(plain_msg)
        assert normalized == plain_msg

    @patch("aiosyslogd.rfc5424.datetime")
    def test_rfc3164_timestamp_conversion_past(self, mock_datetime):
        mock_datetime.now.return_value = datetime(2025, 1, 15)
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
        rfc3164_msg = "<34>Dec 10 22:14:15 mymachine su: test"
        normalized = convert_rfc3164_to_rfc5424(rfc3164_msg)
        assert "2024-12-10T" in normalized

    def test_normalize_to_rfc5424_debug_mode(self, capsys):
        message = "this is not a syslog message"
        normalized = normalize_to_rfc5424(message, debug_mode=True)
        captured = capsys.readouterr()
        assert "Not an RFC 3164 message" in captured.err
        assert normalized == message

    def test_convert_rfc3164_to_rfc5424_timestamp_error(self, capsys):
        message = "<34>Feb 30 22:14:15 mymachine su: test"  # Invalid date
        normalized = convert_rfc3164_to_rfc5424(message, debug_mode=True)
        captured = capsys.readouterr()
        assert "Could not parse RFC-3164 timestamp" in captured.err
        parts = normalized.split()
        assert parts[0] == "<34>1"
        assert re.match(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", parts[1]
        )
        assert parts[2] == "mymachine"
        assert parts[-1] == "test"