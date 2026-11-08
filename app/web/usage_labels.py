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

TABLES: dict[str, dict[str, str]] = {
    "outcomes": OUTCOMES,
    "actors": ACTORS,
    "buckets": BUCKETS,
    "confirmation": CONFIRMATION,
    "closure": CLOSURE,
    "statuses": STATUSES,
}
"""Every code table by name, for the page's macro."""


def label(_: BoundGettext, table: str, code: str) -> str:
    """Return the words for ``code`` of ``table``; an unknown code is shown as it is.

    A code that is not in the table can only be one a newer version wrote; it is a fixed code
    and never free text, so showing it is safe.
    """
    key = TABLES[table].get(code)
    return _(key) if key is not None else code
