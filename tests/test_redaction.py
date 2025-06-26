# tests/test_redaction.py
import pytest
from aiosyslogd.db.logs_utils import redact, REDACTION_CHAR

# --- Test Suite for the redact() function ---


class TestRedaction:
    """
    Test cases for the redact() function in logs_utils.py.
    It verifies that usernames, IP addresses, and MAC addresses are
    correctly identified and redacted.
    """

    @pytest.mark.parametrize(
        "input_log, expected_output",
        [
            # Test case 1: Username with double quotes and equals sign
            (
                'Login attempt for user="john.doe" from 192.168.1.1.',
                f'Login attempt for user="{REDACTION_CHAR * 8}" from {REDACTION_CHAR * 11}.',
            ),
            # Test case 2: Username without quotes
            (
                "Access denied for user=jdoe on console.",
                f"Access denied for user={REDACTION_CHAR * 4} on console.",
            ),
            # Test case 3: Username with single quotes and space separator
            (
                "user 'jane_doe' executed a command.",
                f"user '{REDACTION_CHAR * 8}' executed a command.",
            ),
            # Test case 4: 'username' keyword instead of 'user'
            (
                "The username root is not allowed.",
                f"The username {REDACTION_CHAR * 4} is not allowed.",
            ),
            # Test case 5: Case-insensitivity check
            (
                "Info: User=Admin logged out.",
                f"Info: User={REDACTION_CHAR * 5} logged out.",
            ),
        ],
    )
    def test_redact_usernames(self, input_log, expected_output):
        """Tests various formats of username redaction."""
        assert redact(input_log) == expected_output
        # Test with a fancy redaction character
        fancy_redaction_char = "▒"
        assert redact(
            input_log, fancy_redaction_char
        ) == expected_output.replace(REDACTION_CHAR, fancy_redaction_char)

    def test_redact_ipv4_address(self):
        """Tests redaction of a standard IPv4 address."""
        log = "Connection established from 10.0.0.1 to 8.8.8.8."
        expected = f"Connection established from {REDACTION_CHAR * 8} to {REDACTION_CHAR * 7}."
        assert redact(log) == expected

    @pytest.mark.parametrize(
        "ip_address",
        [
            "2001:0db8:85a3:0000:0000:8a2e:0370:7334",  # Full address
            "fe80::a00:27ff:fe8a:1234",  # Compressed address
            "::1",  # Loopback
            "::ffff:192.0.2.128",  # IPv4-mapped
        ],
    )
    def test_redact_ipv6_addresses(self, ip_address):
        """Tests redaction of various IPv6 address formats."""
        log = f"Device at address {ip_address} responded."
        expected = (
            f"Device at address {REDACTION_CHAR * len(ip_address)} responded."
        )
        assert redact(log) == expected

    @pytest.mark.parametrize(
        "mac_address",
        [
            "00:1A:2B:3C:4D:5E",  # Colon separator
            "00-1a-2b-3c-4d-5e",  # Hyphen separator, lowercase
        ],
    )
    def test_redact_mac_addresses(self, mac_address):
        """Tests redaction of MAC addresses with different separators."""
        log = f"ARP request for MAC {mac_address}."
        expected = f"ARP request for MAC {REDACTION_CHAR * len(mac_address)}."
        assert redact(log) == expected

    def test_redact_multiple_items_in_one_log(self):
        """
        Tests that a log containing multiple types of sensitive information
        is fully redacted.
        """
        log = (
            "User 'tech_admin' logged in from 172.16.31.100 "
            "(MAC: 0A-BC-DE-F0-12-34) and accessed host ::ffff:10.1.1.1."
        )
        expected = (
            f"User '{REDACTION_CHAR * 10}' logged in from {REDACTION_CHAR * 13} "
            f"(MAC: {REDACTION_CHAR * 17}) and accessed host {REDACTION_CHAR * 15}."
        )
        assert redact(log) == expected

    def test_no_sensitive_data(self):
        """
        Tests that a log with no sensitive information remains unchanged.
        """
        log = "System health check: OK. CPU at 25%. Memory at 50%."
        assert redact(log) == log

    def test_redact_ipv6_containing_ipv4(self):
        """
        Ensures that an IPv4-mapped IPv6 address is redacted as a single unit,
        not as two separate addresses.
        """
        # This IPv6 address contains a valid-looking IPv4 address.
        # It's important that the IPv6 pattern matches first and redacts the whole thing.
        log = "Connection from ::ffff:192.168.1.1 was accepted."
        expected = f"Connection from {REDACTION_CHAR * len('::ffff:192.168.1.1')} was accepted."
        assert redact(log) == expected
