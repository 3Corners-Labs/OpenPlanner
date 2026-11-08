"""A warning when one person does an audited thing unusually often within an hour.

A compromised account, a stolen session or an insider shows up as volume: one planner changing
many bookings (ADR 0044), one person asking for many participants' contact details (ADR 0044
§2). Right after recording such an action, its service asks its ``ActivityAlert`` to count the
actor's matching audit events of the past hour, this one included. The action that takes the
count one above the threshold (a class C setting) writes one system event, so a burst raises one
warning, not one per action. It only warns, never refuses: the action goes ahead, because a
false alarm must not stop staff from helping a participant.

    service records the action
        ─► count(actor, actions, detail, past hour) == threshold + 1
        ─► system event <action>, failure, <reason>, "actor:<subject>,<counted>:<count>"

The warning carries the actor's subject identifier and a count, never a participant's data. It
sits at the organisation and group of the action that crossed the threshold, so whoever may read
that group's audit log sees it, and it is shipped with the other system events (ADR 0034). It is
not an alert of ``app.domain.alerts``: that is a lasting condition with a start and an end.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.ports import Transaction

WINDOW = timedelta(hours=1)
"""The period an alert counts over (fixed: each threshold is a number per hour)."""


@dataclass(frozen=True)
class ActivityAlert:
    """One kind of warning about many audited actions by one actor within an hour.

    Attributes:
        action: System event key of the warning (``planner.changes_alert``).
        reason: Reason code of the warning (``too_many_changes``).
        counted: Key that carries the count in the warning's detail (``changes``).
        actions: The audited actions that count (``bookings.manage``).
        detail: When set, only the events with exactly this detail count.
    """

    action: str
    reason: str
    counted: str
    actions: frozenset[str]
    detail: str | None = None

    async def check(
        self,
        tx: Transaction,
        now: datetime,
        actor: str,
        threshold: int,
        organisation: str | None,
        group: str | None,
    ) -> None:
        """Warn when the action just recorded takes ``actor`` one above ``threshold``."""
        count = await tx.count_audit_events(actor, self.actions, now - WINDOW, detail=self.detail)
        if count != threshold + 1:
            return
        await tx.add_audit_event(
            AuditEvent(
                now,
                SYSTEM_ACTOR,
                self.action,
                Outcome.FAILURE,
                self.reason,
                organisation,
                group,
                f"actor:{actor},{self.counted}:{count}",
                Source.SYSTEM,
            )
        )
