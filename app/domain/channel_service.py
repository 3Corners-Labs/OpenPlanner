"""Channel settings use cases: an organisation's SMTP server and SMS gateway (ADR 0021).

Every operation needs ``integrations.manage`` on the organisation (superuser only, HANDOVER
§6), asks ``AdminService.authorize`` first and records an audit event in the same transaction
with the names of what changed, never the values (ADR 0008).

    organisations   integrations.manage   organisations in scope
    overview        integrations.manage   stored settings, whether a secret is set
    save / remove   integrations.manage   change or remove one channel
"""

from dataclasses import dataclass, fields

from app.domain.admin import AdminService, organisation_by_slug, record_change, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.channel_settings import (
    CONFIGURABLE_CHANNELS,
    ChannelConfig,
    ChannelSettings,
    secret_field,
    validate_secret,
)
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Organisation

AUDIT_PREFIX = {ChannelKind.EMAIL: "smtp", ChannelKind.SMS: "sms"}
"""Audit details read ``smtp.host,smtp.password`` or ``sms.remove``."""


@dataclass(frozen=True)
class ChannelOverview:
    """An organisation with its configured channels (``None`` where not configured)."""

    organisation: Organisation
    channels: dict[ChannelKind, ChannelSettings | None]


class ChannelSettingsService:
    """Application service for an organisation's message channels.

    Args:
        uow: Opens database transactions.
        clock: Current time, for ``updated_at`` and audit events.
        admin: Authorises changes and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def organisations(self, principal: Principal) -> list[Organisation]:
        """Return the organisations whose channels ``principal`` may configure."""
        async with self._uow.read() as tx:
            return await tx.list_organisations(scope_for(principal, Permission.INTEGRATIONS_MANAGE))

    async def overview(self, principal: Principal, slug: str) -> ChannelOverview:
        """Return the organisation's channel settings.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            stored = {s.channel: s for s in await tx.get_channel_settings(organisation.id)}
        return ChannelOverview(
            organisation, {channel: stored.get(channel) for channel in CONFIGURABLE_CHANNELS}
        )

    async def save(
        self, principal: Principal, slug: str, config: ChannelConfig, secret: str | None
    ) -> list[str]:
        """Store the channel's settings; return the names of what changed.

        ``secret=None`` keeps the stored secret. A channel that needs a secret (SMTP with a
        username, every SMS gateway) cannot be saved without one.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` (``smtp_credential`` / ``sms_service_key``).
        """
        if secret is not None:
            validate_secret(secret, config.channel)
        await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = next(
                (
                    s
                    for s in await tx.get_channel_settings(organisation.id)
                    if s.channel is config.channel
                ),
                None,
            )
            if config.needs_secret and secret is None and not (current and current.secret_set):
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, secret_field(config.channel))
            changed = [
                f.name
                for f in fields(config)
                if current is None or getattr(current.config, f.name) != getattr(config, f.name)
            ]
            if secret is not None:
                changed.append("secret")
            if changed:
                await tx.save_channel_settings(organisation.id, config, secret, self._clock.now())
                prefix = AUDIT_PREFIX[config.channel]
                await self._audit(tx, principal, slug, ",".join(f"{prefix}.{n}" for n in changed))
        return changed

    async def remove(self, principal: Principal, slug: str, channel: ChannelKind) -> None:
        """Remove the channel's settings and secret; messages on it then fail visibly.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``channel_not_configured``.
        """
        await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            if not await tx.delete_channel_settings(organisation.id, channel):
                raise DomainError(ErrorCode.CHANNEL_NOT_CONFIGURED)
            await self._audit(tx, principal, slug, f"{AUDIT_PREFIX[channel]}.remove")

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``integrations.manage``."""
        require(
            await self._admin.authorize(
                principal, Permission.INTEGRATIONS_MANAGE, organisation_slug=slug
            )
        )

    async def _audit(self, tx: Transaction, principal: Principal, slug: str, detail: str) -> None:
        """Record a channel change of organisation ``slug``."""
        await record_change(
            tx, self._clock.now(), principal, Permission.INTEGRATIONS_MANAGE, slug, None, detail
        )
