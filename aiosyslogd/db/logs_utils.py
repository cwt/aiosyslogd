import re

# --- Regular Expression Patterns ---
# Pattern to match usernames in various formats:
USER_PATTERN = re.compile(
    r"""
    # Match 'user' or 'username' at word boundary
    \b(user(?:name)?)
    # Match optional equals sign or space
    (\s*=\s*|\s+)
    (?:
        # Match opening quote (single or double)
        (["'])
        # Capture content excluding quotes
        ([^"']*?)
        # Match closing quote (same as opening)
        \3
        |
        # Match unquoted username (no spaces or quotes)
        ([^\s"']+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Standard pattern for an IPv4 address
IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Simplified pattern for IPv6, assuming IPv4 is redacted first
IPV6_PATTERN = re.compile(
    r"""
    (?:
        # Standard IPv6: full or compressed
        \b
        (?:
            # Full IPv6 address with 8 groups of 1-4 hex digits
            (?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}
            |
            # Compressed IPv6 address with '::' for zero groups
            (?:[0-9a-fA-F]{1,4}:){1,6}(?::[0-9a-fA-F]{1,4}){1,6}
            |
            # Mixed IPv6 with IPv4-mapped address
            ::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}?
            |
            # Compressed IPv6 with leading zeroes
            (?:[0-9a-fA-F]{1,4}:){1,6}:
            |
            # Loopback address
            ::1
            |
            # Unspecified address
            ::
        )
        \b
        |
        # Link-local: fe80::/10 with %interface
        \b
        fe80:(?::[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]+
        \b
        |
        # IPv4-mapped: ::ffff: with redacted IPv4
        ::ffff:█+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Pattern for IPv6 loopback address (::1) with boundaries
IPV6_LOOPBACK_PATTERN = re.compile(
    r"(?<![:0-9a-fA-F])::1(?![:0-9a-fA-F])", re.IGNORECASE
)

# Standard pattern for a MAC address (':' or '-' as separator)
MAC_PATTERN = re.compile(
    r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b", re.IGNORECASE
)

# --- Performance Enhancement: Combined Pattern ---
# Combine simple patterns (IPv4, MAC, IPv6 Loopback) into one for a single pass.
# The main IPv6 pattern is kept separate because it has a dependency on the
# IPv4 redaction for mapped addresses.
COMBINED_SIMPLE_REDACTION_PATTERN = re.compile(
    "|".join(
        [
            IPV4_PATTERN.pattern,
            MAC_PATTERN.pattern,
            IPV6_LOOPBACK_PATTERN.pattern,
        ]
    ),
    re.IGNORECASE,
)


# Character used to replace sensitive data
REDACTION_CHAR: str = "█"


def redact(message: str, fancy_redaction_char: str | None = None) -> str:
    """
    Finds and redacts sensitive information (usernames, IP addresses, MAC addresses)
    from a log message string.

    Args:
        message: The input log message string.
        fancy_redaction_char: Optional character to use for redaction.

    Returns:
        A new string with sensitive information replaced by '█' or a fancy character.
    """

    # --- Redaction Logic ---
    def user_replacer(match: re.Match) -> str:
        """Replaces matched usernames with redacted version."""
        prefix = f"{match.group(1)}{match.group(2)}"
        if match.group(4) is not None:
            quote = match.group(3)
            value = match.group(4)
            return f"{prefix}{quote}{REDACTION_CHAR * len(value)}{quote}"
        value = match.group(5)
        return f"{prefix}{REDACTION_CHAR * len(value)}"

    def simple_block_replacer(match: re.Match) -> str:
        """Replaces blocked patterns with redacted characters."""
        return REDACTION_CHAR * len(match.group(0))

    # --- Optimized Redaction Order ---
    # Pass 1: Redact simple, non-dependent patterns (IPv4, MAC, IPv6 Loopback).
    redacted_message = COMBINED_SIMPLE_REDACTION_PATTERN.sub(
        simple_block_replacer, message
    )

    # Pass 2: Redact the more complex IPv6 patterns, which may depend on the first pass
    # (e.g., for IPv4-mapped addresses).
    redacted_message = IPV6_PATTERN.sub(simple_block_replacer, redacted_message)

    # Pass 3: Redact usernames, which has its own replacement logic.
    redacted_message = USER_PATTERN.sub(user_replacer, redacted_message)

    # Replace the redaction character if a fancy one is provided (e.g. "▒").
    if fancy_redaction_char:
        redacted_message = redacted_message.replace(
            REDACTION_CHAR, fancy_redaction_char
        )

    return redacted_message
