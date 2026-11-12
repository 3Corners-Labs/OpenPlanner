"""Whether one person may take one piece of work by skill, read inside a transaction (ADR 0121).

Requirement, capability and authority are three relations (ADR 0121 point 5). The matrix has
already decided authority before this runs; this module judges capability against requirement:

    booking.flow ─► flow revision ─► requirement_of(revision, subject)   (what the work needs)
    principal    ─► staff record  ─► list_staff_skills(organisation)     (what the person holds)
                                  ─► eligibility(required, held)         (Eligible | Missing)

The requirement is the revision recorded on the booking, so a later edit of the flow changes
nothing for it; the held skills are read fresh from the open transaction, so a grant revoked a
moment ago counts (A09, A23, A35). Held skills are read at most once and only when something is
required: work without skills, every legacy flow among it, reads no skill at all. Someone
without a staff record or without a membership of the organisation, a superuser included, holds
no skills there. Skills never decide what is visible; callers show every row the matrix allows.
"""

from collections.abc import Iterable

from app.domain.authorization import Principal, Scope
from app.domain.booking import Booking
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef
from app.domain.ports import Transaction
from app.domain.service_flows import FlowRevision, requirement_of
from app.domain.skills import Eligibility, Eligible, Missing, Skill, eligibility


class WorkEligibility:
    """Judges bookings of one organisation for one person, inside one open transaction.

    Args:
        tx: The open transaction; in a claim it is the locked one, so the answer holds until
            the claim is written.
        principal: Who wants to take the work.
        organisation: The slug of the organisation the work belongs to; only skills held there
            count.
    """

    def __init__(self, tx: Transaction, principal: Principal, organisation: str) -> None:
        """Store the transaction, the person and the organisation; nothing is read yet."""
        self._tx = tx
        self._principal = principal
        self._scope = Scope(organisations=frozenset({organisation}))
        self._revisions: dict[FlowRef, FlowRevision | None] = {}
        self._held: frozenset[int] | None = None
        self._skills: dict[int, Skill | None] = {}

    async def required(self, booking: Booking) -> frozenset[int]:
        """Return the skill ids ``booking`` requires: its flow revision plus its subject's."""
        ref = booking.flow
        if ref is None:
            return frozenset()
        if ref not in self._revisions:
            self._revisions[ref] = await self._tx.flow_revision(ref.flow_id, ref.revision)
        subject = booking.routing.subject if booking.routing else None
        return requirement_of(self._revisions[ref], subject)

    async def of(self, booking: Booking) -> Eligibility:
        """Return whether the person may take ``booking`` by skill."""
        required = await self.required(booking)
        if not required:
            return Eligible()
        return eligibility(required, await self._held_skills())

    async def check(self, booking: Booking) -> None:
        """Refuse ``booking`` unless the person holds every skill it requires.

        Raises:
            DomainError: ``not_eligible``; it names no skill (the queue row does that).
        """
        if isinstance(await self.of(booking), Missing):
            raise DomainError(ErrorCode.NOT_ELIGIBLE)

    async def skills(self, ids: Iterable[int]) -> tuple[Skill, ...]:
        """Return the skills ``ids`` (archived ones included) by code, to name what is missing."""
        for skill_id in set(ids) - self._skills.keys():
            self._skills[skill_id] = await self._tx.get_skill(self._scope, skill_id)
        found = (self._skills[skill_id] for skill_id in set(ids))
        return tuple(sorted((s for s in found if s is not None), key=lambda s: s.code))

    async def _held_skills(self) -> frozenset[int]:
        """Return the skills the person holds in the organisation, read once."""
        if self._held is None:
            member = await self._tx.get_staff(self._principal.subject)
            self._held = (
                frozenset()
                if member is None or member.id is None
                else await self._tx.list_staff_skills(self._scope, member.id)
            )
        return self._held
