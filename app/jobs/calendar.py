"""Minimal iCalendar (RFC 5545) entries for confirmation, change, reminder and cancellation.

No personal data goes into the event: summary, team and the management link only.
"""

from datetime import UTC, datetime

_MAX_LINE_OCTETS = 75


def _escape(text: str) -> str:
    """Escape a TEXT value (backslash, semicolon, comma, newline)."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold a content line at 75 octets without splitting UTF-8 characters."""
    out: list[str] = []
    current = ""
    for char in line:
        limit = _MAX_LINE_OCTETS if not out else _MAX_LINE_OCTETS - 1
        if len((current + char).encode()) > limit:
            out.append(current)
            current = char
        else:
            current += char
    out.append(current)
    return "\r\n ".join(out)


def _stamp(moment: datetime) -> str:
    """Format a UTC timestamp as ``YYYYMMDDTHHMMSSZ``."""
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    *,
    uid: str,
    start: datetime,
    end: datetime,
    summary: str,
    description: str,
    url: str | None,
    stamp: datetime,
    cancelled: bool = False,
) -> bytes:
    """Return a calendar with one event; ``cancelled`` produces a CANCEL for the same UID."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OpenPlanner//Booking//EN",
        f"METHOD:{'CANCEL' if cancelled else 'PUBLISH'}",
        "BEGIN:VEVENT",
        f"UID:{_escape(uid)}",
        f"DTSTAMP:{_stamp(stamp)}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
    ]
    if url:
        lines.append(f"URL:{url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode()
