"""The address of an organisation's privacy statement (ADR 0123 point 5, step 6b).

The call page says that an external video service carries the call and links to the
organisation's privacy statement, which names that service (the page never does). The address is
the organisation's own setting ``privacy_statement_url`` (class D); the rule for what it may be is
here and nowhere else, so the portal, the command line, the environment and the page agree:

    valid_privacy_url(value)   the check of the setting, at save and at the environment
    privacy_link(value)        the address as the page may link to it, or ``None``

The page checks the stored value again where it renders it, so a value that got into the
database some other way (an older version, a hand edit) never becomes a link. Pure Python.
"""

from urllib.parse import urlsplit

MAX_LENGTH = 2048
"""The longest address the setting accepts, in characters (a fixed rule, not a setting: it is the
common limit of the address bar of the browsers the page serves)."""


FORBIDDEN = frozenset("\\\"'<>`")
"""Characters an address may not carry: a backslash (browsers read it as a slash) and the ones
that close or open an attribute or a tag. Percent-encoding says them safely."""


def valid_privacy_url(value: str) -> bool:
    """Return whether ``value`` may be an organisation's privacy statement address.

    An ``https`` address of at most ``MAX_LENGTH`` characters with a host, without credentials,
    spaces, control characters, backslashes, quotes, angle brackets or backticks. An empty value
    is valid (the setting is optional: the page then shows its line without a link).
    """
    if value == "":
        return True
    if len(value) > MAX_LENGTH or value != value.strip():
        return False
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch in FORBIDDEN for ch in value):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port  # raises for a port that is no number or out of range
    except ValueError:  # an unparsable authority or port
        return False
    return parts.scheme == "https" and bool(host) and "@" not in parts.netloc


def privacy_link(value: str | None) -> str | None:
    """Return the address the call page may link to, ``None`` for no link.

    ``None`` for an empty or missing value and for one that ``valid_privacy_url`` refuses.
    """
    if not value or not valid_privacy_url(value):
        return None
    return value
