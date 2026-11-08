"""The fallback of a contact-center group to the own queue (ADR 0082 points 11 to 13).

A group may choose ``dispatch_fallback = own_queue``. Its bookings then reach the own queue of
ADR 0043, flagged *Terugval*, in two ways:

- **per booking**: its dispatch job fails for good (after the retries of ADR 0068);
- **for the group**: while the organisation's contact-center health is ``down`` (ADR 0082
  point 7) the group is in fallback; pending dispatch jobs skip with reason ``fallback`` and
  put their bookings in the queue. When the health is ``ok`` again (the health already waited
  ``integration_recovery_minutes``), the fallback ends and the bookings not yet claimed are
  dispatched after all.

``dispatch_kind`` never changes: the fallback is a state. Each transition is the system event
``dispatch.fallback`` (failure ``on``, success ``off``), an alert of ADR 0061, and a group
admin may end it by hand (audited). A team space (``fallback_space_room_id``) gets a message on
activation and one line per booking: start time, first name and a link to the queue, never
the host join link (claiming gives that, ADR 0043 point 6). The participant notices nothing.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from app.domain.admin import AdminService, record_change, require
from app.domain.alerts import FALLBACK_EVENT
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.authorization import Permission, Principal, Scope
from app.domain.booking import Booking
from app.domain.calls import EventKind
from app.domain.connection_health import HealthEntry, HealthState, Platform
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.ports import Clock, IntegrationError, UnitOfWork
from app.domain.scheduling import Group

ON = "on"
"""Reason of the ``dispatch.fallback`` failure."""
DISPATCH_FAILED = "dispatch_failed"
"""Detail of ``fallback_queued`` for a booking whose own dispatch failed for good."""
CONTACT_CENTER_DOWN = "contact_center_down"
"""Detail of ``fallback_queued`` for a booking queued while its group is in fallback."""
FALLBACK = "fallback"
"""Skip reason of a dispatch job while its group is in fallback."""


class SpacePoster(Protocol):
    """Posts a markdown message to a Webex space with the organisation's bot (ADR 0082 point 12)."""

    async def post(self, organisation_id: int, room_id: str, markdown: str) -> None:
        """Post ``markdown`` to ``room_id``; raise ``IntegrationError`` on failure."""
        ...


class FallbackTexts(Protocol):
    """The fixed texts of the fallback messages, in the organisation's locale (ADR 0009)."""

    def activated(self, group: Group, locale: str) -> str:
        """Return the message that the group is in fallback, with a link to its queue."""
        ...

    def queued(self, group: Group, booking: Booking, locale: str) -> str:
        """Return the line of one booking: start time, first name, link to the queue."""
        ...


class FallbackService:
    """Puts bookings in the own queue and switches a group's fallback on and off.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises ending a fallback by hand.
        integration: Writes timeline events and the call status.
        dispatch: Dispatches a booking to the contact center after all (``CallService``).
        poster: Posts to a team space; ``None`` posts nothing.
        texts: The messages for the space.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        integration: IntegrationService,
        dispatch: Callable[[Booking, Group], Awaitable[object]] | None = None,
        poster: SpacePoster | None = None,
        texts: FallbackTexts | None = None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._integration = integration
        self._dispatch = dispatch
        self._poster = poster
        self._texts = texts

    async def queue_booking(self, booking: Booking, group: Group, detail: str) -> None:
        """Put ``booking`` in the group's own queue, flagged as fallback, and tell the team."""
        if booking.id is None:  # pragma: no cover - bookings are stored
            return
        await self._integration.record(booking.id, EventKind.FALLBACK_QUEUED, detail=detail)
        texts = self._texts
        if texts is not None:
            await self._post(group, lambda locale: texts.queued(group, booking, locale))

    async def sweep(self, health: Iterable[HealthEntry]) -> list[str]:
        """Switch fallbacks on and off by the organisations' contact-center health.

        Returns:
            The slugs of the groups whose fallback changed.
        """
        down = {
            e.organisation
            for e in health
            if e.platform is Platform.WXCC and e.state is HealthState.DOWN
        }
        healthy = {
            e.organisation
            for e in health
            if e.platform is Platform.WXCC and e.state is HealthState.OK
        }
        changed: list[str] = []
        async with self._uow.read() as tx:
            groups = [s for s in await tx.list_groups(Scope(everything=True)) if s.group.falls_back]
        for summary in groups:
            group, organisation = summary.group, summary.organisation_slug
            if organisation in down and not group.fallback_active:
                await self._switch_on(group, organisation)
                changed.append(group.slug)
            elif organisation in healthy and group.fallback_active:
                await self._switch_off(group, organisation, None)
                changed.append(group.slug)
        return changed

    async def end(self, principal: Principal, slug: str) -> bool:
        """End the group's fallback by hand; return whether it was on.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        authorization = require(
            await self._admin.authorize(principal, Permission.BOOKINGS_MANAGE, group_slug=slug)
        )
        if authorization.group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        group = authorization.group.group
        if not group.fallback_active:
            return False
        await self._switch_off(group, authorization.group.organisation_slug, principal)
        return True

    async def _switch_on(self, group: Group, organisation: str) -> None:
        """Put the group in fallback, record it and tell the team."""
        now = self._clock.now()
        async with self._uow.locked() as tx:
            await tx.save_group_fallback(group.id, now)
            await tx.add_audit_event(_event(group, organisation, Outcome.FAILURE, now))
        texts = self._texts
        if texts is not None:
            await self._post(group, lambda locale: texts.activated(group, locale))

    async def _switch_off(
        self, group: Group, organisation: str, principal: Principal | None
    ) -> None:
        """End the fallback; bookings not yet claimed go to the contact center after all."""
        now = self._clock.now()
        async with self._uow.locked() as tx:
            await tx.save_group_fallback(group.id, None)
            await tx.add_audit_event(_event(group, organisation, Outcome.SUCCESS, now))
            if principal is not None:
                await record_change(
                    tx,
                    now,
                    principal,
                    Permission.BOOKINGS_MANAGE,
                    organisation,
                    group.slug,
                    "fallback.off",
                )
            waiting = await tx.fallback_bookings(group.id, now)
        restored = replace(group, fallback_active_since=None)
        for booking in waiting:
            if booking.id is None or booking.claim is not None:
                continue
            await self._integration.record(booking.id, EventKind.FALLBACK_ENDED)
            if self._dispatch is not None:
                try:
                    await self._dispatch(booking, restored)
                except IntegrationError:
                    continue  # its dispatch job retries; the booking left the queue already

    async def _post(self, group: Group, write: Callable[[str], str]) -> None:
        """Post to the group's own space in its organisation's locale, when it has a space.

        A failure never stops the fallback: the queue shows the booking all the same.
        """
        if self._poster is None or group.fallback_space_room_id is None:
            return
        async with self._uow.read() as tx:
            organisation = await tx.get_organisation_by_id(group.organisation_id)
        locale = organisation.default_locale if organisation else "nl"
        try:
            await self._poster.post(
                group.organisation_id, group.fallback_space_room_id, write(locale)
            )
        except IntegrationError:
            return


def _event(group: Group, organisation: str, outcome: Outcome, now: datetime) -> AuditEvent:
    """Return the ``dispatch.fallback`` event of a transition (codes only)."""
    on = outcome is Outcome.FAILURE
    return AuditEvent(
        now,
        SYSTEM_ACTOR,
        FALLBACK_EVENT,
        outcome,
        ON if on else "",
        organisation,
        group.slug,
        "fallback.on" if on else "fallback.off",
        Source.SYSTEM,
    )
