"""The use cases of the display links: make, list, revoke, open a screen, keep it open (step 14).

    WallboardLinkService.create    ``reports.wallboard.link.manage`` ─► a token, shown once
    WallboardLinkService.exchange  the first request, with the token  ─► a screen session
    WallboardLinkService.session   every later request, with the secret ─► the link, or a refusal

The token is 32 random bytes; only its ``sha256`` is stored, and the first request consumes it in
one conditional update, so a link that leaks later is worthless and a replaced screen needs a new
one. The request then carries a second secret of its own (the session), valid until the link
expires or is revoked, from the networks the link allows and, when it pins, from the one it
pinned. A refusal is one deduplicated audit event per link and period; the address that was
refused is never stored or logged (ADR 0117 point 20, R10).

The screen is told only *why* it was refused as a code; the page says it in words. A request for a
link that does not exist, is used, expired or revoked is refused the same way, so a guess learns
nothing.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, WallboardLinkStore, WallboardLinkUnitOfWork
from app.domain.wallboard_link import (
    DAYS_FIELD,
    ENABLED_FIELD,
    MAX_NAME,
    NAME_FIELD,
    NETWORKS_FIELD,
    REFUSAL_EVERY,
    TOUCH_EVERY,
    Refusal,
    WallboardLink,
    decide,
    hash_token,
    inside,
    new_token,
    parse_networks,
    pinned_range,
    same_hash,
)

STARTED = "wallboard.session_started"
"""Audit action of the first use of a link: a screen was opened on a device."""
REFUSED = "wallboard.session_refused"
"""Audit action of a request refused for its address; at most one per link and period."""


@dataclass(frozen=True)
class Opened:
    """A link that was used for the first time.

    Attributes:
        secret: The screen's session secret, to set as a cookie; stored only as a hash.
        link: The link as it was.
    """

    secret: str
    link: WallboardLink


@dataclass(frozen=True)
class Created:
    """A link that was made.

    Attributes:
        token: The token, shown once and never kept.
        link: The stored link.
    """

    token: str
    link: WallboardLink


class WallboardLinkService:
    """Makes and revokes display links and decides every request that carries one.

    Args:
        uow: Opens transactions with the link store.
        admin: Authorises the manager's use cases and writes the audit events.
        clock: Current time.
        enabled: ``reporting_wallboard_display_links_enabled`` of an organisation id (class D).
        default_networks: The organisation's default ACL (``reporting_wallboard_link_networks``,
            class D), parsed; an empty tuple is any network.
        max_days: ``reporting_wallboard_link_max_days`` (class C), read each time.
    """

    def __init__(
        self,
        uow: WallboardLinkUnitOfWork,
        admin: AdminService,
        clock: Clock,
        enabled: Callable[[int], bool],
        default_networks: Callable[[int], tuple[str, ...]],
        max_days: Callable[[], int],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._admin = admin
        self._clock = clock
        self._enabled = enabled
        self._defaults = default_networks
        self._max_days = max_days

    async def create(
        self,
        principal: Principal,
        organisation_slug: str,
        *,
        group_slug: str | None,
        name: str,
        days: int,
        networks: str,
        pin: bool,
    ) -> Created:
        """Make a link for the organisation's wallboard or one group's, and audit it.

        Raises:
            DomainError: ``not_permitted``; ``group_not_found``; ``invalid_configuration`` with
                the field ``enabled`` (display links are off), ``name``, ``days`` (beyond the
                longest life) or ``networks`` (not ranges, too many, or wider than the
                organisation's default).
        """
        organisation_id = await self._admin.authorize_organisation(
            principal, Permission.REPORTS_WALLBOARD_LINK_MANAGE, organisation_slug
        )
        if not self._enabled(organisation_id):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, ENABLED_FIELD)
        clean = name.strip()
        if not clean or len(clean) > MAX_NAME:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, NAME_FIELD)
        if not 1 <= days <= self._max_days():
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, DAYS_FIELD)
        default = self._defaults(organisation_id)
        ranges = parse_networks(networks) or default
        if not inside(ranges, default):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, NETWORKS_FIELD)
        now = self._clock.now()
        token = new_token()
        async with self._uow.locked() as tx:
            group_id = None
            if group_slug is not None:
                group_id = await tx.group_id_in_organisation(organisation_id, group_slug)
                if group_id is None:
                    raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            link = WallboardLink(
                None,
                organisation_id,
                organisation_slug,
                group_id,
                group_slug,
                clean,
                hash_token(token),
                principal.subject,
                now,
                now + timedelta(days=days),
                networks=ranges,
                pin=pin,
            )
            link_id = await tx.add_wallboard_link(link)
        await self._audit_manage(principal, link, f"create:{link_id}")
        return Created(token, replace(link, id=link_id))

    async def rows(self, principal: Principal, organisation_slug: str) -> list[WallboardLink]:
        """Return the organisation's links, newest first.

        Raises:
            DomainError: ``not_permitted``.
        """
        await self._admin.authorize_organisation(
            principal, Permission.REPORTS_WALLBOARD_LINK_MANAGE, organisation_slug
        )
        scope = scope_for(principal, Permission.REPORTS_WALLBOARD_LINK_MANAGE)
        async with self._uow.read() as tx:
            return await tx.list_wallboard_links(scope, organisation_slug)

    async def revoke(self, principal: Principal, organisation_slug: str, link_id: int) -> None:
        """Revoke a link of the organisation and audit it; its screen ends within one poll.

        Raises:
            DomainError: ``not_permitted``; ``group_not_found`` for a link that is not this
                organisation's (not told apart from one that does not exist).
        """
        await self._admin.authorize_organisation(
            principal, Permission.REPORTS_WALLBOARD_LINK_MANAGE, organisation_slug
        )
        async with self._uow.locked() as tx:
            link = await tx.get_wallboard_link(link_id)
            if link is None or link.organisation_slug != organisation_slug:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            changed = await tx.revoke_wallboard_link(link_id, self._clock.now())
        if changed:
            await self._audit_manage(principal, link, f"revoke:{link_id}")

    async def exchange(self, token: str, address: str) -> Opened:
        """Use a link up for the first time and open its screen session.

        Raises:
            DomainError: ``not_permitted`` with the field of a ``Refusal``; a refused address is
                audited once per link and period.
        """
        now = self._clock.now()
        refusal = Refusal.LINK
        record: WallboardLink | None = None
        opened: Opened | None = None
        async with self._uow.locked() as tx:
            link = await tx.get_wallboard_link_by_token(hash_token(token))
            if link is not None and same_hash(link.token_hash, token):
                found = decide(
                    link, now, self._enabled(link.organisation_id), address, first_use=True
                )
                pin = pinned_range(address) if link.pin else None
                if found is None and link.pin and pin is None:
                    found = Refusal.NETWORK  # a pin needs an address to pin
                if found is None:
                    secret = new_token()
                    if await tx.consume_wallboard_link(link.id or 0, now, hash_token(secret), pin):
                        opened = Opened(secret, link)
                    else:
                        found = Refusal.LINK  # another request used it first
                if found is not None:
                    refusal = found
                    record = await self._note_refusal(tx, link, now)
        if opened is not None:
            await self._admin.record(
                f"display:{opened.link.id}",
                STARTED,
                Outcome.SUCCESS,
                "",
                opened.link.organisation_slug,
                opened.link.group_slug,
                f"link:{opened.link.id}",
            )
            return opened
        await self._audit_refusal(record, refusal)
        raise DomainError(ErrorCode.NOT_PERMITTED, refusal.value)

    async def session(self, secret: str, address: str) -> WallboardLink:
        """Return the link behind a screen's session secret, or refuse the request.

        Called on every request that carries the cookie, a poll included: revocation, expiry,
        the switch of the organisation, the ACL and the pin all apply at once. *Last used* is
        written at most every ``TOUCH_EVERY``.

        Raises:
            DomainError: ``not_permitted`` with the field of a ``Refusal``.
        """
        now = self._clock.now()
        async with self._uow.read() as tx:
            link = await tx.get_wallboard_link_by_session(hash_token(secret))
        if link is None or not same_hash(link.session_hash, secret):
            raise DomainError(ErrorCode.NOT_PERMITTED, Refusal.LINK.value)
        refusal = decide(link, now, self._enabled(link.organisation_id), address, first_use=False)
        if refusal is not None:
            due = link.last_refused_at is None or now - link.last_refused_at >= REFUSAL_EVERY
            if refusal in (Refusal.NETWORK, Refusal.PIN) and due:  # no write lock while it is not
                async with self._uow.locked() as tx:
                    record = await self._note_refusal(tx, link, now)
                await self._audit_refusal(record, refusal)
            raise DomainError(ErrorCode.NOT_PERMITTED, refusal.value)
        if link.last_used_at is None or now - link.last_used_at >= TOUCH_EVERY:
            async with self._uow.locked() as tx:
                await tx.touch_wallboard_link(link.id or 0, now, now - TOUCH_EVERY)
        return link

    async def _note_refusal(
        self, store: WallboardLinkStore, link: WallboardLink, now: datetime
    ) -> WallboardLink | None:
        """Mark the refusal on the link; return the link when an event is due, else ``None``."""
        wrote = await store.mark_wallboard_link_refused(link.id or 0, now, now - REFUSAL_EVERY)
        return link if wrote else None

    async def _audit_refusal(self, link: WallboardLink | None, refusal: Refusal | None) -> None:
        """Write the one audit event of a refused address; the address is not in it."""
        if link is None or refusal is None:
            return
        await self._admin.record(
            f"display:{link.id}",
            REFUSED,
            Outcome.DENIED,
            refusal.value,
            link.organisation_slug,
            link.group_slug,
            f"link:{link.id}",
        )

    async def _audit_manage(self, principal: Principal, link: WallboardLink, detail: str) -> None:
        """Audit a creation or a revocation: the permission is the action, no token in it."""
        await self._admin.record(
            principal.subject,
            Permission.REPORTS_WALLBOARD_LINK_MANAGE.value,
            Outcome.SUCCESS,
            "",
            link.organisation_slug,
            link.group_slug,
            detail,
        )
