"""The words for the codes in a usage report (ADR 0117 step 5).

The domain report carries codes (an outcome, a bucket, an actor); each code has one i18n key
here, written out literally so the catalogue test sees every key is used. A page asks
``label(_, table, code)``; nothing in a report is free text.
"""

from app.i18n import BoundGettext

OUTCOMES: dict[str, str] = {
    "completed": "reports.outcome.completed",
    "no_show": "reports.outcome.no_show",
    "cancelled_by_participant": "reports.outcome.cancelled_by_participant",
    "cancelled_by_planner": "reports.outcome.cancelled_by_planner",
    "cancelled_by_closure": "reports.outcome.cancelled_by_closure",
    "cancelled_by_system": "reports.outcome.cancelled_by_system",
    "expired_unconfirmed": "reports.outcome.expired_unconfirmed",
}
ACTORS: dict[str, str] = {
    "participant": "reports.actor.participant",
    "planner": "reports.actor.planner",
    "closure": "reports.actor.closure",
    "system": "reports.actor.system",
    "unknown": "reports.actor.unknown",
}
BUCKETS: dict[str, str] = {
    "0": "reports.bucket.0",
    "1": "reports.bucket.1",
    "2": "reports.bucket.2",
    "3": "reports.bucket.3",
    "4": "reports.bucket.4",
    "5": "reports.bucket.5",
    "6": "reports.bucket.6",
    "7": "reports.bucket.7",
    "8": "reports.bucket.8",
    "9": "reports.bucket.9",
}
CONFIRMATION: dict[str, str] = {
    "confirmed": "reports.confirmation.confirmed",
    "expired_unconfirmed": "reports.confirmation.expired",
}
CLOSURE: dict[str, str] = {
    "suspended": "reports.closure.suspended",
    "resumed": "reports.closure.resumed",
}
STATUSES: dict[str, str] = {
    "confirmed": "reports.status.confirmed",
    "cancelled": "reports.status.cancelled",
    "completed": "reports.status.completed",
    "no_show": "reports.status.no_show",
    "suspended": "reports.status.suspended",
    "awaiting_rebook": "reports.status.awaiting_rebook",
    "pending_confirmation": "reports.status.pending_confirmation",
}

DISPATCH: dict[str, str] = {
    "contact_center": "reports.dispatch.contact_center",
    "openplanner_queue": "reports.dispatch.openplanner_queue",
}
BOOKING_KINDS: dict[str, str] = {
    "scheduled": "reports.kind.scheduled",
    "direct": "reports.kind.direct",
}
CHANNELS: dict[str, str] = {
    "email": "reports.channel.email",
    "sms": "reports.channel.sms",
    "whatsapp": "reports.channel.whatsapp",
}
PURPOSES: dict[str, str] = {
    "confirmation": "reports.purpose.confirmation",
    "reminder": "reports.purpose.reminder",
    "change": "reports.purpose.change",
    "cancellation": "reports.purpose.cancellation",
}
FAILURES: dict[str, str] = {
    "task_failed": "reports.failure.task_failed",
    "meeting_failed": "reports.failure.meeting_failed",
    "integration_error": "reports.failure.integration_error",
}
REBOOK: dict[str, str] = {
    "proposed.planner": "reports.rebook.proposed_planner",
    "proposed.system": "reports.rebook.proposed_assistant",
}
FILL: dict[str, str] = {
    "empty": "reports.fill.empty",
    "under_half": "reports.fill.under_half",
    "under_full": "reports.fill.under_full",
    "full": "reports.fill.full",
}
SHORTFALL: dict[str, str] = {
    "open": "reports.shortfall.open",
    "accepted": "reports.shortfall.accepted",
    "resolved": "reports.shortfall.resolved",
}
TRIAL: dict[str, str] = {
    "created": "reports.trial.created",
    "joined": "reports.trial.joined",
    "entered": "reports.trial.entered",
    "failed": "reports.trial.failed",
}
LOCALES: dict[str, str] = {
    "nl": "reports.locale.nl",
    "en": "reports.locale.en",
}
WEEKDAYS: dict[str, str] = {
    "0": "reports.weekday.0",
    "1": "reports.weekday.1",
    "2": "reports.weekday.2",
    "3": "reports.weekday.3",
    "4": "reports.weekday.4",
    "5": "reports.weekday.5",
    "6": "reports.weekday.6",
}

STATES: dict[str, str] = {
    "sent": "reports.channels.sent",
    "failed": "reports.channels.failed",
}

TABLES: dict[str, dict[str, str]] = {
    "outcomes": OUTCOMES,
    "actors": ACTORS,
    "buckets": BUCKETS,
    "confirmation": CONFIRMATION,
    "closure": CLOSURE,
    "statuses": STATUSES,
    "dispatch": DISPATCH,
    "booking_kinds": BOOKING_KINDS,
    "channels": CHANNELS,
    "purposes": PURPOSES,
    "failures": FAILURES,
    "rebook": REBOOK,
    "fill": FILL,
    "shortfall": SHORTFALL,
    "trial": TRIAL,
    "locales": LOCALES,
    "weekdays": WEEKDAYS,
    "states": STATES,
}
"""Every code table by name, for the page's macro."""


def label(_: BoundGettext, table: str, code: str) -> str:
    """Return the words for ``code`` of ``table``; an unknown code is shown as it is.

    A code that is not in the table can only be one a newer version wrote; it is a fixed code
    and never free text, so showing it is safe. A duration bucket below zero (a host who joined
    early) reads as the mirrored bucket followed by *early*; a failure code of the form
    ``part.reason`` reads as the part's words and the reason code.
    """
    if table == "buckets" and code.startswith("-") and code[1:].isdigit():
        mirrored = BUCKETS.get(str(-int(code) - 1))
        if mirrored is not None:
            return _("reports.bucket.early", range=_(mirrored))
    if table == "failures" and code not in FAILURES and "." in code:
        part, _sep, reason = code.partition(".")
        return f"{label(_, table, part)}: {reason}"
    key = TABLES[table].get(code)
    return _(key) if key is not None else code


def duration_text(_: BoundGettext, seconds: int, *, signed: bool = False) -> str:
    """Return a mean duration in words: seconds, minutes, hours or days, whichever reads best.

    With ``signed`` a negative value reads as *early* and a positive one as *late* (metric Q3).
    """
    magnitude = abs(seconds)
    if magnitude < 120:
        text = _("reports.mean.seconds", n=magnitude)
    elif magnitude < 7200:
        text = _("reports.mean.minutes", n=round(magnitude / 60))
    elif magnitude < 172800:
        text = _("reports.mean.hours", n=round(magnitude / 3600))
    else:
        text = _("reports.mean.days", n=round(magnitude / 86400))
    if not signed or seconds == 0:
        return text
    return _("reports.mean.early" if seconds < 0 else "reports.mean.late", value=text)
