"""Channel settings use cases: an organisation's SMTP server and SMS gateway (ADR 0022).

Every operation needs ``integrations.manage`` on the organisation (superuser only, HANDOVER
§6), asks ``AdminService.authorize`` first and records an audit event in the same transaction
with the names of what changed, never the values (ADR 0008).

    organisations   integrations.manage   organisations in scope
    overview        integrations.manage   stored settings, whether a secret is set
    save / remove   integrations.manage   change or remove one channel
    send_test_email integrations.manage   one email through the server the worker would use
    check_sender_domain integrations.manage   SPF, DKIM and DMARC of the sender in effect (ADR 0073)
"""

from collections.abc import Callable
from dataclasses import dataclass, fields

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.channel_settings import (
    CONFIGURABLE_CHANNELS,
    ChannelConfig,
    ChannelSettings,
    SmtpSettings,
    secret_field,
    validate_secret,
    validate_test_recipient,
)
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.mail_domain import DomainReport, check_domain, sender_domain
from app.domain.organisations import organisation_by_slug
from app.domain.ports import (
    Clock,
    IntegrationError,
    MailDomainResolver,
    OutboundMessage,
    SenderDirectory,
    Transaction,
    UnitOfWork,
)
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
        senders: The senders the worker uses, for test emails; ``None`` has nothing to test.
        resolver: Looks up DNS records for the sender domain check (ADR 0073); ``None`` has
            nothing to check.
        fallback_sender: Returns the installation's fallback ``From`` (``SMTP_HOST`` /
            ``MAIL_FROM``) or ``None``, read at each check so portal changes apply live.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        senders: SenderDirectory | None = None,
        resolver: MailDomainResolver | None = None,
        fallback_sender: Callable[[], str | None] | None = None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._senders = senders
        self._resolver = resolver
        self._fallback_sender = fallback_sender

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

        ``secret=None`` keeps the stored secret, but only for the same server and provider
        (``secret_binding``): a password or API key is never sent to a host it was not entered
        for (ADR 0028). A channel that needs a secret (SMTP with a username, every SMS
        gateway) cannot be saved without one.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` (``smtp_credential`` / ``sms_api_key``).
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
            keeps_secret = (
                current is not None
                and current.secret_set
                and current.config.secret_binding == config.secret_binding
            )
            if config.needs_secret and secret is None and not keeps_secret:
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

    async def send_test_email(
        self, principal: Principal, slug: str, recipient: str, subject: str, text: str
    ) -> str | None:
        """Send one email to ``recipient`` exactly as the worker would for this organisation.

        Uses the organisation's own SMTP server, else the installation's fallback. The audit
        event names the outcome (``smtp.test.ok`` or ``smtp.test.<code>``), never the address.

        Returns:
            ``None`` when the server accepted the message, else the failure code of the
            sender (``unauthorized``, ``unreachable``, ``tls_failed``, …).

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, ``invalid_configuration``
                (``test_recipient``) or ``channel_not_configured`` (no mail server at all).
        """
        validate_test_recipient(recipient)
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
        sender = (
            await self._senders.sender(organisation.id, ChannelKind.EMAIL)
            if self._senders is not None
            else None
        )
        if sender is None:
            raise DomainError(ErrorCode.CHANNEL_NOT_CONFIGURED)
        message = OutboundMessage(ChannelKind.EMAIL, recipient, subject, text)
        try:
            await sender.send(message)
        except IntegrationError as error:
            failure: str | None = error.code
        else:
            failure = None
        async with self._uow.locked() as tx:
            await self._audit(tx, principal, slug, f"smtp.test.{failure or 'ok'}")
        return failure

    async def check_sender_domain(self, principal: Principal, slug: str) -> DomainReport:
        """Look up SPF, DKIM and DMARC of the sender domain in effect (ADR 0073).

        The organisation's own SMTP settings give the domain and the DKIM selector; without
        them the installation's fallback sender gives the domain and DKIM stays unchecked. The
        audit event names the finding (``smtp.domain_check.<verdict>``), not the records.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``channel_not_configured`` (no sender at all, or nothing to resolve with).
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            stored = next(
                (
                    s.config
                    for s in await tx.get_channel_settings(organisation.id)
                    if isinstance(s.config, SmtpSettings)
                ),
                None,
            )
        fallback = self._fallback_sender() if self._fallback_sender else None
        if stored is not None:
            address, selector = stored.sender_address, stored.dkim_selector
        elif fallback:
            address, selector = fallback, None
        else:
            raise DomainError(ErrorCode.CHANNEL_NOT_CONFIGURED)
        if self._resolver is None:
            raise DomainError(ErrorCode.CHANNEL_NOT_CONFIGURED)
        report = await check_domain(self._resolver, sender_domain(address), selector)
        async with self._uow.locked() as tx:
            await self._audit(tx, principal, slug, f"smtp.domain_check.{report.finding}")
        return report

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
