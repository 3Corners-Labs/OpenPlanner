"""Subjects and routing use cases: an organisation admin's routing settings of a group (ADR 0081).

Every change asks ``AdminService.authorize`` for ``group.edit`` on the group first, runs in
one locked transaction, saves the group and is audited with the code or source that changed,
never a label or a value.

    view              the group's subjects, attributes, mapping and the subject limit
    add_subject       a new subject at the end, at most ``max_subjects_per_group``
    update_subject    its labels; the code never changes (bookings and reports keep it)
    remove_subject    refused while a staffing block offers it (ADR 0081 point 4)
    move_subject      one place up or down
    set_attributes    switch catalogue attributes on or off
    set_mapping       the routing variable of one source, or none

A subject removed from the list stays on the bookings that chose it, as a code; the admin
screens show the code when no label is left.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.lookups import group_summary_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.routing import (
    RESERVED_FROM_NOW,
    Attribute,
    GroupRouting,
    RouteVariable,
    Subject,
    check_subject_count,
)
from app.domain.scheduling import GroupSummary
from app.domain.tunable import value_of

_PERMISSION = Permission.GROUP_EDIT


@dataclass(frozen=True)
class GroupRoutingView:
    """A group with its routing, for the admin screen.

    ``bound`` are the subject codes a staffing block offers; they cannot be removed.
    """

    summary: GroupSummary
    routing: GroupRouting
    max_subjects: int
    bound: frozenset[str] = frozenset()

    @property
    def can_add(self) -> bool:
        """Whether another subject fits under the platform's limit."""
        return len(self.routing.subjects) < self.max_subjects


class RoutingService:
    """Application service for a group's subjects, routing attributes and mapping.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
        max_subjects: The platform's limit per group (class C, ``max_subjects_per_group``).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        max_subjects: int | Callable[[], int],
    ) -> None:
        """Store the collaborators and the limit."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._max = max_subjects

    async def view(self, principal: Principal, slug: str) -> GroupRoutingView:
        """Return the group's routing.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        summary = await self._admin.authorize_group(principal, _PERMISSION, slug)
        async with self._uow.read() as tx:
            bound = await _bound(tx, summary.group.id)
        return GroupRoutingView(summary, summary.group.routing, value_of(self._max), bound)

    async def add_subject(self, principal: Principal, slug: str, subject: Subject) -> None:
        """Add ``subject`` at the end of the list.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived`` or
                ``invalid_configuration`` naming ``subject_code`` (taken) or ``subjects``
                (limit reached).
        """

        def change(routing: GroupRouting, _: frozenset[str]) -> tuple[GroupRouting, str]:
            if routing.subject(subject.code) is not None:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "subject_code")
            added = routing.with_subjects((*routing.subjects, subject))
            check_subject_count(added, value_of(self._max))
            return added, f"subject.add,code:{subject.code}"

        await self._change(principal, slug, change)

    async def update_subject(
        self, principal: Principal, slug: str, code: str, labels: Mapping[str, str]
    ) -> None:
        """Replace the labels of the subject ``code``.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived`` or
                ``invalid_configuration`` naming ``subject_code`` or a label.
        """

        def change(routing: GroupRouting, _: frozenset[str]) -> tuple[GroupRouting, str]:
            _existing(routing, code)
            updated = Subject.create(code, labels)
            subjects = tuple(updated if s.code == code else s for s in routing.subjects)
            return routing.with_subjects(subjects), f"subject.update,code:{code}"

        await self._change(principal, slug, change)

    async def remove_subject(self, principal: Principal, slug: str, code: str) -> None:
        """Remove the subject ``code``; its mapping entry goes with it.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived`` or
                ``invalid_configuration`` naming ``subject_code`` or ``subject_in_use`` (a
                staffing block offers it).
        """

        def change(routing: GroupRouting, bound: frozenset[str]) -> tuple[GroupRouting, str]:
            _existing(routing, code)
            if code in bound:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "subject_in_use")
            kept = tuple(s for s in routing.subjects if s.code != code)
            return routing.with_subjects(kept), f"subject.remove,code:{code}"

        await self._change(principal, slug, change)

    async def move_subject(self, principal: Principal, slug: str, code: str, step: int) -> None:
        """Move the subject ``code`` one place up (``step < 0``) or down.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived`` or
                ``invalid_configuration`` naming ``subject_code``.
        """

        def change(routing: GroupRouting, _: frozenset[str]) -> tuple[GroupRouting, str] | None:
            _existing(routing, code)
            subjects = list(routing.subjects)
            index = next(i for i, s in enumerate(subjects) if s.code == code)
            target = min(max(index + (-1 if step < 0 else 1), 0), len(subjects) - 1)
            if target == index:
                return None
            subjects.insert(target, subjects.pop(index))
            return routing.with_subjects(subjects), f"subject.move,code:{code}"

        await self._change(principal, slug, change)

    async def set_attributes(
        self, principal: Principal, slug: str, attributes: Collection[Attribute]
    ) -> None:
        """Switch exactly ``attributes`` on; the mapping of one switched off goes with it.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``group_archived``.
        """

        def change(routing: GroupRouting, _: frozenset[str]) -> tuple[GroupRouting, str] | None:
            updated = routing.with_attributes(attributes)
            if updated == routing:
                return None
            names = "+".join(a.value for a in updated.attributes) or "none"
            return updated, f"attributes.set,on:{names}"

        await self._change(principal, slug, change)

    async def set_mapping(
        self,
        principal: Principal,
        slug: str,
        source: str,
        name: str,
        values: Mapping[str, str],
    ) -> None:
        """Map ``source`` to the variable ``name``; an empty name sends nothing for it.

        ``values`` are the submitted values per code; only the source's own codes with a
        non-empty value are read, inside the lock, so a subject removed meanwhile drops out.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``group_archived`` or
                ``invalid_configuration`` naming ``mapping``, ``variable_name`` (also for a name
                OpenPlanner sends itself, ``RESERVED_FROM_NOW``) or ``variable_value``.
        """

        def change(routing: GroupRouting, _: frozenset[str]) -> tuple[GroupRouting, str] | None:
            if name in RESERVED_FROM_NOW:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "variable_name")
            variable = (
                RouteVariable(
                    name,
                    tuple(
                        (code, values[code]) for code in routing.codes(source) if values.get(code)
                    ),
                )
                if name
                else None
            )
            updated = routing.with_mapping(source, variable)
            if updated == routing:
                return None
            return updated, f"mapping.set,source:{source}"

        await self._change(principal, slug, change)

    async def _change(
        self,
        principal: Principal,
        slug: str,
        change: Callable[[GroupRouting, frozenset[str]], tuple[GroupRouting, str] | None],
    ) -> None:
        """Authorise, apply ``change`` in a locked transaction, save the group and audit it."""
        require(await self._admin.authorize(principal, _PERMISSION, group_slug=slug))
        async with self._uow.locked() as tx:
            summary = await group_summary_by_slug(tx, slug)
            group = summary.group
            if group.is_archived:
                raise DomainError(ErrorCode.GROUP_ARCHIVED)
            result = change(group.routing, await _bound(tx, group.id))
            if result is None:
                return
            routing, detail = result
            await tx.save_group_routing(group.id, routing)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                _PERMISSION,
                summary.organisation_slug,
                slug,
                detail,
            )


async def _bound(tx: Transaction, group_id: int) -> frozenset[str]:
    """Return the subject codes a weekly block or an exception of the group offers."""
    rules, overrides = await tx.get_schedule_items(group_id)
    windows = [r.window for r in rules] + [o.window for o in overrides if o.window]
    return frozenset(code for window in windows for code in window.subjects)


def _existing(routing: GroupRouting, code: str) -> Subject:
    """Return the subject ``code`` or raise ``invalid_configuration`` naming ``subject_code``."""
    subject = routing.subject(code)
    if subject is None:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "subject_code")
    return subject
