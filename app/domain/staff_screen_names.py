"""Setting a staff member's screen name, and finding the name of who took a call (ADR 0123 step 5c).

    set_screen_name(principal, id, text) ── staff.manage ──► ``parse_screen_name`` ──► the record
                                                             ── audit (that it changed, not to what)
    label_of(claimed_by, mode)  ── the claim's pseudonym ──► the staff id ──► the record, read now
                                                             ──► ``staff_label`` ──► name or none

The first half is the write: who may set a screen name is ``staff.manage`` inside the staff
overview's scope (the person must be one the administrator sees, SQL-filtered by the same scope as
the list), or the person themselves on *My profile* (step 5d, ``set_own_screen_name``, found from
the session's own subject, ``rights.view``); an erased record keeps no name. Audit events carry
the person's subject and a reason code (``set`` or ``cleared``), never the name; an own change is
the one whose actor equals the subject.

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
from app.domain.authorization import Permission, Principal, Scope, Target, decide, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.guest_access import StaffNameMode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.screen_name import NO_LABEL, StaffLabel, parse_screen_name, staff_label
from app.domain.staff import SCREEN_NAME_EVENT, StaffFilter, StaffMember, StaffStatus
from app.domain.tokens import staff_ids_by_pseudonym

REBUILD_FLOOR = timedelta(seconds=60)
"""The least time between two rebuilds of the pseudonym map. It is no setting (ADR 0042): it is a
bound on our own table scans that changes no outcome an administrator chooses, because a name is
read from the record at every ask (see the module's text) and a member who is new since the last
rebuild is found at the first miss once the last rebuild is older than the floor. A claim that
matches nobody (a booking dispatched to the contact center, a pseudonym of a key that is gone)
may thus make the directory be scanned once a minute at most."""

_OWN_RECORD = Scope(everything=True)
"""The scope of a person's write to their own record (step 5d), found by the session's subject.

It is no reach of its own and grants nothing: it only decides where the audit event is written
(every organisation of the person, not only those a viewer sees). The right to write is
``rights.view``, as for *My sessions* (ADR 0098 point 9a)."""

SET = "set"
CLEARED = "cleared"
"""The reason codes of a successful audit event."""


class ScreenNameResult(StrEnum):
    """What a request to set a screen name led to."""

    DONE = "done"
    """A name was set (or replaced)."""
    CLEARED = "cleared"
    """The name was removed with an empty text."""
    UNCHANGED = "unchanged"
    OUT_OF_SCOPE = "out_of_scope"
    ERASED = "erased"
    NO_RECORD = "no_record"
    """Only for the person's own write: the login has no staff record (step 5d)."""


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
            member = await _manageable(tx, scope, member_id)
            if member is None:
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
            return await _store(tx, principal.subject, member, scope, name, now)

    async def may_set(self, principal: Principal, member_id: int) -> bool:
        """Return whether ``principal`` may set the screen name of ``member_id`` (the page asks).

        The question of ``set_screen_name``: ``staff.manage`` for a person inside that
        permission's scope (not merely one the viewer may see), and a record that is not erased.
        """
        scope = scope_for(principal, Permission.STAFF_MANAGE)
        if scope.is_empty:
            return False
        async with self._uow.read() as tx:
            member = await _manageable(tx, scope, member_id)
        return member is not None and member.status is not StaffStatus.ERASED

    async def own(self, subject: str) -> StaffMember | None:
        """Return the staff record of ``subject`` for their own *My profile* page, or ``None``.

        The record is written at every login, so ``None`` is only a person whose login was never
        recorded (a session that outlived a restored database, say).
        """
        async with self._uow.read() as tx:
            return await tx.get_staff(subject)

    async def set_own_screen_name(self, principal: Principal, text: str) -> ScreenNameResult:
        """Set the logged-in person's own screen name, or clear it with an empty text (step 5d).

        The record is found from ``principal.subject`` and nothing else: there is no member
        number to choose. The right is ``rights.view``, which every admin holds, as for *My
        sessions*. The rule, the erasure check and the audit event are those of the manager's
        write; the event's actor and subject are the same person, in every organisation of
        the person (the scope of the write is the person's own record, not a viewer's).

        Returns:
            ``NO_RECORD`` when the login has no staff record, else as ``set_screen_name``
            (never ``OUT_OF_SCOPE``).

        Raises:
            DomainError: ``not_permitted`` without ``rights.view``, and
                ``invalid_configuration`` for a text that is not a screen name.
        """
        if not decide(principal, Permission.RIGHTS_VIEW, Target.everything()).allowed:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        name = parse_screen_name(text)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            member = await tx.get_staff(principal.subject)
            if member is None:
                return ScreenNameResult.NO_RECORD
            return await _store(tx, principal.subject, member, _OWN_RECORD, name, now)

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
        found = staff_ids_by_pseudonym(self._keys, ((m.id, m.subject) for m in members))
        self._index = _Index(found, now)
        return self._index.ids.get(pseudonym)


async def _manageable(tx: Transaction, scope: Scope, member_id: int) -> StaffMember | None:
    """Return the person if ``scope`` (that of ``staff.manage``) covers them, else ``None``.

    The one place that says whether a manager may set a member's screen name; the write and
    the page's offer of the form both use it.
    """
    found = await tx.list_staff(scope, StaffFilter(id=member_id))
    return found[0] if found else None


async def _store(
    tx: Transaction,
    actor: str,
    member: StaffMember,
    scope: Scope,
    name: str | None,
    now: datetime,
) -> ScreenNameResult:
    """Write ``name`` on ``member`` unless erased or unchanged, and audit it (both ways in).

    ``scope`` decides in which organisations the event is written: those of the person that the
    writer sees (a manager) or all of them (the person themselves).
    """
    if member.status is StaffStatus.ERASED:
        return ScreenNameResult.ERASED
    if member.screen_name == name:
        return ScreenNameResult.UNCHANGED
    await tx.set_staff_screen_name(member.subject, name)
    await _audit(tx, actor, member, scope, now, SET if name else CLEARED)
    return ScreenNameResult.DONE if name else ScreenNameResult.CLEARED


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
