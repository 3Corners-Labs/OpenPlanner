"""Setting a staff member's screen name, and finding the name of who took a call (ADR 0123 step 5c).

    set_screen_name(principal, id, text) ── staff.manage ──► ``parse_screen_name`` ──► the record
                                                             ── audit (that it changed, not to what)
    label_of(claimed_by, mode)  ── the claim's pseudonym ──► the staff id ──► the record, read now
                                                             ──► ``staff_label`` ──► name or none

The first half is the write: who may set a screen name is ``staff.manage`` inside the staff
overview's scope (the person must be one the administrator sees, SQL-filtered by the same scope as
the list), and an erased record keeps no name. Audit events carry the person's subject and a reason
code (``set`` or ``cleared``), never the name.

The second half is what the participant's call page asks of our own server. A booking's claim names
who accepted it only by a pseudonym (``tokens.agent_pseudonym``), a keyed hash that cannot be turned
back into a subject, so the staff member is found by computing the pseudonyms of the directory. That
scan must not run on every poll: the pseudonym-to-id map is kept here, in this one place, and is
rebuilt at most once per ``REBUILD_FLOOR`` and only when a pseudonym is not in it (a new staff
member, or a claim by someone this installation does not know). The map holds pseudonyms and row
ids and nothing else; the name is read from the record by its primary key on every ask, so a changed
or cleared screen name, an erasure and a suspension show at once and the cache can never be stale
about them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.guest_access import StaffNameMode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.screen_name import NO_LABEL, StaffLabel, parse_screen_name, staff_label
from app.domain.staff import SCREEN_NAME_EVENT, StaffFilter, StaffMember, StaffStatus
from app.domain.tokens import agent_pseudonyms

REBUILD_FLOOR = timedelta(seconds=60)
"""The least time between two rebuilds of the pseudonym map. Fixed: it only bounds how often a
claim that matches nobody (a booking dispatched to the contact center, a pseudonym of a key that
is gone) may make the directory be scanned; it changes what is shown for no one, because a name is
read from the record each time (see the module's text)."""

SET = "set"
CLEARED = "cleared"
"""The reason codes of a successful audit event."""


class ScreenNameResult(StrEnum):
    """What a request to set a screen name led to."""

    DONE = "done"
    UNCHANGED = "unchanged"
    OUT_OF_SCOPE = "out_of_scope"
    ERASED = "erased"


@dataclass(frozen=True)
class _Index:
    """The pseudonyms of the directory, by pseudonym, and when they were computed."""

    ids: dict[str, int]
    built_at: datetime


class StaffScreenNames:
    """Sets the screen name of a staff member and names who accepted a booking.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        keys: The keys of the agents' pseudonyms (``keys.agent_pseudonym_key`` and the previous
            one while a rotation lasts), the same as ``StaffUsage`` is given.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, keys: Sequence[bytes]) -> None:
        """Store the collaborators; the map of pseudonyms starts empty."""
        self._uow = uow
        self._clock = clock
        self._keys = tuple(keys)
        self._index: _Index | None = None

    async def set_screen_name(
        self, principal: Principal, member_id: int, text: str
    ) -> ScreenNameResult:
        """Set the screen name of the person with ``member_id``, or clear it with an empty text.

        Raises:
            DomainError: ``not_permitted`` without ``staff.manage`` anywhere, and
                ``invalid_configuration`` on ``screen_name`` for a text that is not a screen
                name (a ``ScreenNameError`` with the code of the rule it breaks).
        """
        scope = scope_for(principal, Permission.STAFF_MANAGE)
        if scope.is_empty:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        name = parse_screen_name(text)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            found = await tx.list_staff(scope, StaffFilter(id=member_id))
            if not found:
                await tx.add_audit_event(
                    AuditEvent(
                        now,
                        principal.subject,
                        Permission.STAFF_MANAGE.value,
                        Outcome.DENIED,
                        "out_of_scope",
                    )
                )
                return ScreenNameResult.OUT_OF_SCOPE
            member = found[0]
            if member.status is StaffStatus.ERASED:
                return ScreenNameResult.ERASED
            if member.screen_name == name:
                return ScreenNameResult.UNCHANGED
            await tx.set_staff_screen_name(member.subject, name)
            await _audit(tx, principal.subject, member, scope, now, SET if name else CLEARED)
        return ScreenNameResult.DONE

    async def label_of(self, claimed_by: str | None, mode: StaffNameMode) -> StaffLabel:
        """Return what the participant is told of who accepted a booking.

        ``claimed_by`` is the claim's pseudonym, ``None`` while nobody accepted (or after the
        booking's purge). No name without a claim, for the ``term`` mode, for a pseudonym that
        matches no staff record, and for an erased record.
        """
        if claimed_by is None or mode is StaffNameMode.TERM:
            return NO_LABEL
        staff_id = await self._staff_id(claimed_by)
        if staff_id is None:
            return NO_LABEL
        async with self._uow.read() as tx:
            member = await tx.get_staff_by_id(staff_id)
        if member is None or member.status is StaffStatus.ERASED:
            return NO_LABEL
        return staff_label(mode, member.screen_name, member.display_name)

    async def _staff_id(self, pseudonym: str) -> int | None:
        """Return the row id of the staff record behind ``pseudonym``, or ``None``.

        The map is rebuilt at most once per ``REBUILD_FLOOR``, and only when it lacks the pseudonym.
        """
        index = self._index
        if index is not None and pseudonym in index.ids:
            return index.ids[pseudonym]
        now = self._clock.now()
        if index is not None and now - index.built_at < REBUILD_FLOOR:
            return None
        async with self._uow.read() as tx:
            members = await tx.list_staff(Scope(everything=True), StaffFilter())
        self._index = _Index(self._pseudonyms(members), now)
        return self._index.ids.get(pseudonym)

    def _pseudonyms(self, members: Sequence[StaffMember]) -> dict[str, int]:
        """Return the id of every member of the directory under each of its pseudonyms."""
        return {
            pseudonym: member.id
            for member in members
            if member.id is not None
            for pseudonym in agent_pseudonyms(self._keys, member.subject)
        }


async def _audit(
    tx: Transaction,
    actor: str,
    member: StaffMember,
    scope: Scope,
    now: datetime,
    reason: str,
) -> None:
    """Write the event once per organisation of ``member`` inside ``scope`` (global without)."""
    places: list[str | None] = [*member.within(scope).organisations] or [None]
    for organisation in places:
        await tx.add_audit_event(
            AuditEvent(
                now,
                actor,
                SCREEN_NAME_EVENT,
                Outcome.SUCCESS,
                reason,
                organisation,
                detail=f"subject:{member.subject}"[:255],
            )
        )
