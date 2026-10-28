"""Minimal iCalendar (RFC 5545) entries for confirmation, change, reminder and cancellation.

No personal data goes into the participant's event: summary, team and the management link only.
The invite to an own-queue team (ADR 0043) is a ``REQUEST`` (RFC 5546) with the team's address
as organizer and attendee, so the event lands in the team's calendar without answers; its
``CANCEL`` withdraws it. The ``SEQUENCE`` rises with each change.
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
    invite: str | None = None,
    sequence: int = 0,
) -> bytes:
    """Return a calendar with one event; ``cancelled`` produces a CANCEL for the same UID.

    ``invite`` makes it a ``REQUEST`` to that address (organizer and attendee at once).
    """
    method = "CANCEL" if cancelled else "REQUEST" if invite else "PUBLISH"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OpenPlanner//Booking//EN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{_escape(uid)}",
        f"DTSTAMP:{_stamp(stamp)}",
        f"SEQUENCE:{sequence}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
    ]
    if invite:
        lines += [
            f"ORGANIZER:mailto:{invite}",
            f"ATTENDEE;CUTYPE=GROUP;RSVP=FALSE:mailto:{invite}",
        ]
    if url:
        lines.append(f"URL:{url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode()
