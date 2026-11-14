"""Writes the facts about a team member in the transaction of their event (ADR 0117 point 9).

Only while the organisation has switched the team view on (``reporting_staff_view_enabled``, class
D, off by default): with it off nothing about a person is written at all. A claim names its agent
only by a pseudonym; it is matched to the organisation's staff here and nowhere else.

    claim from the own queue ─► ``StaffUsage.claimed``  ─► ``queue.claimed`` of the claimer
    host joined / meeting ended of a claimed booking
                             ─► ``StaffUsage.call``     ─► the same facts, with the claimer's id
"""

from collections.abc import Callable, Sequence
from datetime import datetime

from app.domain.authorization import Scope
from app.domain.booking import Booking
from app.domain.calls import EventKind
from app.domain.ports import Transaction
from app.domain.scheduling import Group
from app.domain.staff import StaffFilter
from app.domain.tokens import staff_ids_by_pseudonym
from app.domain.usage import FactKind, StaffFact, UsageFact, hour_of

_CALL_EVENTS: dict[EventKind, FactKind] = {
    EventKind.HOST_JOINED: FactKind.CALL_HOST_JOINED,
    EventKind.MEETING_ENDED: FactKind.CALL_ENDED,
}
"""The call events whose facts are also a team member's, and the kind each one has."""


class StaffUsage:
    """Writes the facts about a team member, when the organisation has switched that on.

    Args:
        keys: The keys of the agents' pseudonyms (``keys.agent_pseudonym_key`` and the previous
            one): a claim names its agent only by a pseudonym, which is matched to the
            organisation's staff here and nowhere else.
        enabled: ``reporting_staff_view_enabled`` of an organisation id (class D, read each
            time).
    """

    def __init__(self, keys: Sequence[bytes], enabled: Callable[[int], bool]) -> None:
        """Store the keys and the setting."""
        self._keys = tuple(keys)
        self._enabled = enabled

    async def claimed(self, tx: Transaction, subject: str, group: Group, at: datetime) -> None:
        """Record a claim from the own queue by the staff member with ``subject``.

        A person the directory does not know (a login that left no staff row) makes no fact.
        """
        if not self._enabled(group.organisation_id):
            return
        member = await tx.get_staff(subject)
        if member is None or member.id is None:
            return
        await tx.record_usage(
            StaffFact(
                FactKind.QUEUE_CLAIMED, hour_of(at), group.organisation_id, group.id, member.id
            )
        )

    async def call(
        self,
        tx: Transaction,
        booking: Booking,
        group: Group,
        kind: EventKind,
        facts: Sequence[UsageFact],
    ) -> None:
        """Record the team member's share of the call facts ``facts`` of a call event.

        Only a booking an agent claimed has a team member: a call dispatched to the contact
        center is answered by someone this installation cannot name, so it has none. The
        member is the staff entry of the organisation whose pseudonym the claim carries.
        """
        wanted = _CALL_EVENTS.get(kind)
        if wanted is None or booking.claim is None or not self._enabled(group.organisation_id):
            return
        mine = [f for f in facts if f.kind is wanted]
        if not mine:
            return
        staff_id = await self._staff_of(tx, booking.claim.claimed_by)
        if staff_id is None:
            return
        for fact in mine:
            await tx.record_usage(
                StaffFact(
                    fact.kind,
                    fact.bucket_hour,
                    group.organisation_id,
                    group.id,
                    staff_id,
                    fact.value_seconds,
                )
            )

    async def _staff_of(self, tx: Transaction, pseudonym: str) -> int | None:
        """Return the id of the staff entry whose pseudonym is ``pseudonym``, if there is one."""
        members = await tx.list_staff(Scope(everything=True), StaffFilter())
        found = staff_ids_by_pseudonym(self._keys, ((m.id, m.subject) for m in members))
        return found.get(pseudonym)
