"""A warning when one person does an audited thing unusually often within an hour.

A compromised account, a stolen session or an insider shows up as volume: one planner changing
many bookings (ADR 0044), one person asking for many participants' contact details (ADR 0044
§2). Each alert counts one person's successful audit events of one action over the past hour.
The event that takes the count one above the threshold (a class C setting) writes one system
event, so a burst raises one warning, not one per action. It only warns, never refuses: the
action goes ahead, because a false alarm must not stop staff from helping a participant.

    writer records its event ─► CHANGES_ALERT.check(tx, event, threshold)
        ─► count(event.actor, event.action, success, past hour) == threshold + 1
        ─► system event <alert>, failure, <reason>, "actor:<subject>,<counted>:<count>"

The rule that keeps this exact: every writer of a counted event runs the check right after
writing it, in the same transaction. A counted event written without the check could carry the
count past threshold + 1 unseen, and that burst would raise nothing. A refused attempt is not a
success, so it never counts. The writers today:

    CHANGES_ALERT  bookings.manage  a planner's change (``planner_service``), a retried call
                                    (``call_retry``), a fallback ended by hand (``fallback``)
    REVEALS_ALERT  bookings.view    a request for contact details (``participant_contact``),
                                    the only audited success under that permission

The warning carries the actor's subject identifier and a count, never a participant's data. It
sits at the organisation and group of the event that crossed the threshold, so whoever may read
that group's audit log sees it, and it is shipped with the other system events (ADR 0034). The
count covers every scope the person works in. It is not an alert of ``app.domain.alerts``: that
is a lasting condition with a start and an end.
"""

from dataclasses import dataclass
from datetime import timedelta

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.ports import Transaction

WINDOW = timedelta(hours=1)
"""The period an alert counts over (fixed: each threshold is a number per hour)."""


@dataclass(frozen=True)
class ActivityAlert:
    """One kind of warning about many audited actions by one person within an hour.

    Attributes:
        action: System event key of the warning (``planner.changes_alert``).
        reason: Reason code of the warning (``too_many_changes``).
        counted: Key that carries the count in the warning's detail (``changes``).
    """

    action: str
    reason: str
    counted: str

    async def check(self, tx: Transaction, event: AuditEvent, threshold: int) -> None:
        """Warn when ``event``, just recorded, takes its actor one above ``threshold``.

        Counts the actor's successful events with ``event.action`` since an hour before
        ``event.at``, ``event`` included.
        """
        count = await tx.count_audit_successes(event.actor, event.action, event.at - WINDOW)
        if count != threshold + 1:
            return
        await tx.add_audit_event(
            AuditEvent(
                event.at,
                SYSTEM_ACTOR,
                self.action,
                Outcome.FAILURE,
                self.reason,
                event.organisation,
                event.group,
                f"actor:{event.actor},{self.counted}:{count}",
                Source.SYSTEM,
            )
        )


CHANGES_ALERT = ActivityAlert("planner.changes_alert", "too_many_changes", "changes")
"""Above ``planner_changes_alert_per_hour`` changes under ``bookings.manage`` (ADR 0044)."""

REVEALS_ALERT = ActivityAlert(
    "booking.contact_reveals_alert", "too_many_contact_reveals", "reveals"
)
"""Above ``contact_reveals_alert_per_hour`` requests for contact details (ADR 0044 §2)."""
