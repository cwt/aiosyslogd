import re

# --- Regular Expression Patterns ---
# This pattern is designed to catch multiple username formats:
# - user="john.doe" (with quotes)
# - user=johndoe (without quotes)
# - user johndoe (space as separator)
# It uses a VERBOSE flag to allow for comments within the regex pattern.
USER_PATTERN = re.compile(
    r"""
        \b(user(?:name)?)     # Match 'user' or 'username' as a whole word (Group 1)
        (\s*=\s*|\s+)         # Match separator: equals sign or one/more spaces (Group 2)
        (?:                   # Start a non-capturing group for the actual value
            (["'])              # Match an opening quote (double or single) (Group 3)
            (.*?)               # Match the content inside the quotes (non-greedy) (Group 4)
            \3                  # Match the corresponding closing quote
            |                   # OR
            ([^\s"']+)          # Match an unquoted value (not a space or quote) (Group 5)
        )
        """,
    re.IGNORECASE | re.VERBOSE,
)

# A comprehensive pattern for most standard IPv6 formats.
# Handles full, compressed (::), and IPv4-mapped addresses.
IPV6_PATTERN = re.compile(
    r"(?:(?:[0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:(?:(?::[0-9a-fA-F]{1,4}){1,6})|:(?:(?::[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(?::[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(?:ffff(?::0{1,4}){0,1}:){0,1}(?:(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9])|(?:[0-9a-fA-F]{1,4}:){1,4}:(?:(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9]))",
    re.IGNORECASE,
)

# Standard pattern for an IPv4 address.
IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


# Standard pattern for a MAC address (':' or '-' as separator).
MAC_PATTERN = re.compile(r"(?:[0-9a-fA-F]{2}[:-]){5}(?:[0-9a-fA-F]{2})")

# Character used to replace sensitive data.
REDACTION_CHAR = "█"


def redact(message: str) -> str:
    """
    Finds and redacts sensitive information (usernames, IP addresses, MAC addresses)
    from a log message string.

    Args:
        message: The input log message string.

    Returns:
        A new string with sensitive information replaced by '█' characters.
    """

    # --- Redaction Logic ---

    # Redact user information using a replacement function (a "replacer")
    # to handle the different captured groups from the complex regex.
    def user_replacer(match: re.Match) -> str:
        """Determines what to do with a matched user pattern."""
        # The first part of the match, e.g., "user=" or "user "
        prefix = f"{match.group(1)}{match.group(2)}"

        if match.group(4) is not None:
            # This was a quoted match, like user="john"
            quote = match.group(3)
            value = match.group(4)
            return f"{prefix}{quote}{REDACTION_CHAR * len(value)}{quote}"
        else:
            # This was an unquoted match, like user=john or user john
            value = match.group(5)
            return f"{prefix}{REDACTION_CHAR * len(value)}"

    redacted_message = USER_PATTERN.sub(user_replacer, message)

    # Redact IP and MAC addresses using a simpler lambda replacer.
    # IMPORTANT: Redact IPv6 first, as it can contain an IPv4 address.
    redacted_message = IPV6_PATTERN.sub(
        lambda match: REDACTION_CHAR * len(match.group(0)), redacted_message
    )
    redacted_message = IPV4_PATTERN.sub(
        lambda match: REDACTION_CHAR * len(match.group(0)), redacted_message
    )
    redacted_message = MAC_PATTERN.sub(
        lambda match: REDACTION_CHAR * len(match.group(0)), redacted_message
    )

    return redacted_message
