"""The readiness checklist of a group and an organisation (ADR 0047 §5, build step 5).

What is still missing before a group or an organisation can take bookings, computed from data
that already exists; the checklist keeps no state of its own. Each item names a stable key;
the admin portal chooses the text and the tab that fixes it, so the domain carries no
user-facing text (CLAUDE.md).

A group is ready when it has staffing that is still valid, its organisation has a channel
that can send, and, for a group that calls a platform (a Webex host or a contact-center
mapping), its latest connection check did not fail. Its texts always have a value (the
catalogue, then the organisation and the group, ADR 0009), so that item is never missing: it
is done with texts of its own, or runs on the defaults. An organisation is ready when it has a
channel that can send, its own branding and a contact block for closures.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.domain.admin import AdminService, require
from app.domain.authorization import Permission, Principal
from app.domain.branding import Branding
from app.domain.channel_settings import ChannelSettings
from app.domain.checks import CheckStatus, GroupCheck
from app.domain.integration_check import has_chain
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, UnitOfWork
from app.domain.scheduling import Group, ScheduleRule


class Item(StrEnum):
    """One line of a checklist; the value is the stable key the portal builds its text from."""

    STAFFING = "staffing"
    TEXTS = "texts"
    CHANNELS = "channels"
    CHECK = "check"
    BRANDING = "branding"
    CONTACT = "contact"


class State(StrEnum):
    """Where an item stands; only ``MISSING`` keeps an object from taking bookings."""

    DONE = "done"
    """Nothing left to do."""

    MISSING = "missing"
    """Needed before bookings can be taken."""

    DEFAULTS = "defaults"
    """Not missing: the defaults apply (the texts of a group without texts of its own)."""

    NOT_NEEDED = "not_needed"
    """Does not apply to this object (a check for a group that calls no platform)."""


@dataclass(frozen=True)
class Line:
    """One item of a checklist and where it stands."""

    item: Item
    state: State


@dataclass(frozen=True)
class Checklist:
    """The lines of one object's checklist, in the order the portal shows them."""

    lines: tuple[Line, ...]

    @property
    def missing(self) -> int:
        """How many lines are missing."""
        return sum(line.state is State.MISSING for line in self.lines)


def _done(condition: bool) -> State:
    """Return ``DONE`` when ``condition`` holds, else ``MISSING``."""
    return State.DONE if condition else State.MISSING


def has_staffing(rules: Sequence[ScheduleRule], today: date) -> bool:
    """Whether any weekly rule still applies on ``today`` (the group's date) or later."""
    return any(rule.applies_from(today) for rule in rules)


def can_send(channels: Sequence[ChannelSettings]) -> bool:
    """Whether at least one configured channel is ready to send."""
    return any(channel.ready for channel in channels)


def group_checklist(
    group: Group,
    *,
    staffed: bool,
    own_texts: bool,
    sends: bool,
    check: GroupCheck | None,
) -> Checklist:
    """Return the checklist of ``group`` from the facts the service gathered.

    Args:
        group: The group, to see whether it calls a platform.
        staffed: Whether it has staffing that still applies.
        own_texts: Whether it has message texts of its own.
        sends: Whether its organisation has a channel that can send.
        check: Its latest connection check, if it was ever checked.
    """
    if not has_chain(group):
        check_state = State.NOT_NEEDED
    else:
        check_state = _done(check is not None and check.overall is not CheckStatus.FAIL)
    return Checklist(
        (
            Line(Item.STAFFING, _done(staffed)),
            Line(Item.TEXTS, State.DONE if own_texts else State.DEFAULTS),
            Line(Item.CHANNELS, _done(sends)),
            Line(Item.CHECK, check_state),
        )
    )


def organisation_checklist(*, sends: bool, branding: Branding, contact: bool) -> Checklist:
    """Return the checklist of an organisation.

    Args:
        sends: Whether it has a channel that can send.
        branding: Its branding; the defaults count as not done.
        contact: Whether it has a contact block for closures.
    """
    return Checklist(
        (
            Line(Item.CHANNELS, _done(sends)),
            Line(Item.BRANDING, _done(branding != Branding())),
            Line(Item.CONTACT, _done(contact)),
        )
    )


class ReadinessService:
    """Gathers the facts of a checklist inside the reader's rights.

    Args:
        uow: Opens database transactions.
        clock: Current time, for staffing that has ended.
        admin: Authorises every read and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def group(self, principal: Principal, slug: str) -> Checklist:
        """Return the checklist of group ``slug``.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        group = (await self._admin.authorize_group(principal, Permission.GROUP_VIEW, slug)).group
        async with self._uow.read() as tx:
            today = group.today(self._clock.now())
            rules, _ = await tx.get_schedule(group.id, today, today)
            texts = await tx.get_group_texts(group.id)
            channels = await tx.get_channel_settings(group.organisation_id)
            check = await tx.get_group_check(group.id)
        return group_checklist(
            group,
            staffed=has_staffing(rules, today),
            own_texts=bool(texts),
            sends=can_send(channels),
            check=check,
        )

    async def organisation(self, principal: Principal, slug: str) -> Checklist:
        """Return the checklist of organisation ``slug``.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        require(
            await self._admin.authorize(
                principal, Permission.ORGANISATION_VIEW, organisation_slug=slug
            )
        )
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            channels = await tx.get_channel_settings(organisation.id)
            contact = await tx.get_contact_block(organisation.id)
        return organisation_checklist(
            sends=can_send(channels), branding=organisation.branding, contact=contact is not None
        )
