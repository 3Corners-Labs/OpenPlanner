"""Build the ``MessageSender`` of an organisation's channel from its stored settings (ADR 0022).

The worker asks per message job; the settings and the decrypted secret are read fresh each
time, so a change in the admin portal applies to the next message without a restart.

    email  stored SMTP settings      → SmtpMailSender
           none stored, SMTP_HOST set → SmtpMailSender with the environment fallback
    sms    stored SMS settings        → the adapter of their provider (SMS_GATEWAYS)
    otherwise                         → None (the job fails as ``channel_not_configured``)
"""

from collections.abc import Callable

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.sms import SmsGatewayConfig
from app.adapters.smtp import SmtpConfig, SmtpMailSender
from app.adapters.webex_connect_sms import WebexConnectSmsSender
from app.config import Settings
from app.domain.channel_settings import SmsProvider, SmsSettings, SmtpSecurity, SmtpSettings
from app.domain.channels import ChannelKind
from app.domain.ports import MessageSender
from app.domain.tunable import value_of

SMS_GATEWAYS: dict[SmsProvider, Callable[[SmsGatewayConfig, httpx.AsyncClient], MessageSender]] = {
    SmsProvider.WEBEX_CONNECT: WebexConnectSmsSender,
}
"""The adapter per SMS provider (ADR 0028); a test checks that every provider has one."""


class ConfiguredSenders:
    """Finds the sender for a channel of an organisation.

    Args:
        uow: Reads channel settings and secrets (needs the settings key for secrets).
        http: Shared HTTP client for the SMS gateway.
        fallback_mail: The platform's fallback SMTP server for organisations without their own,
            or a function returning it, read at each send (portal changes apply live, ADR 0042).
    """

    def __init__(
        self,
        uow: SqlUnitOfWork,
        http: httpx.AsyncClient,
        fallback_mail: SmtpConfig | None | Callable[[], SmtpConfig | None],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._http = http
        self._fallback_mail = fallback_mail

    async def sender(self, organisation_id: int, channel: ChannelKind) -> MessageSender | None:
        """Return the sender of ``channel`` for the organisation, or ``None`` if there is none."""
        async with self._uow.read() as tx:
            stored = next(
                (s for s in await tx.get_channel_settings(organisation_id) if s.channel is channel),
                None,
            )
            secret = await tx.get_channel_secret(organisation_id, channel) if stored else None
        if stored is None:
            fallback = value_of(self._fallback_mail)
            if channel is ChannelKind.EMAIL and fallback is not None:
                return SmtpMailSender(fallback)
            return None
        if not stored.ready:
            return None
        config = stored.config
        if isinstance(config, SmtpSettings):
            return SmtpMailSender(smtp_config(config, secret))
        return SMS_GATEWAYS[config.provider](sms_config(config, secret or ""), self._http)


def fallback_mail(settings: Settings) -> SmtpConfig | None:
    """Return the platform's fallback SMTP server, or ``None`` when ``SMTP_HOST`` is not set.

    Used for organisations without their own SMTP settings (ADR 0022).
    """
    if not settings.smtp_host:
        return None
    return SmtpConfig(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=settings.mail_from,
        starttls=settings.smtp_starttls,
        implicit_tls=settings.smtp_implicit_tls,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
    )


def smtp_config(settings: SmtpSettings, password: str | None) -> SmtpConfig:
    """Return the adapter configuration for an organisation's SMTP settings."""
    return SmtpConfig(
        host=settings.host,
        port=settings.port,
        sender=settings.sender,
        starttls=settings.security is SmtpSecurity.STARTTLS,
        implicit_tls=settings.security is SmtpSecurity.TLS,
        username=settings.username,
        password=password,
        reply_to=settings.reply_to_address,
    )


def sms_config(settings: SmsSettings, api_key: str) -> SmsGatewayConfig:
    """Return the adapter configuration for an organisation's SMS settings."""
    return SmsGatewayConfig(
        api_base_url=settings.api_base_url,
        api_key=api_key,
        sender_id=settings.sender_id,
        country_code=settings.country_code,
    )
