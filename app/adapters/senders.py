"""Build the ``MessageSender`` of an organisation's channel from its stored settings (ADR 0022).

The worker asks per message job; the settings and the decrypted secret are read fresh each
time, so a change in the admin portal applies to the next message without a restart.

    email  stored SMTP settings      → SmtpMailSender
           none stored, SMTP_HOST set → SmtpMailSender with the environment fallback
    sms    stored Connect settings    → WebexConnectSmsSender
    otherwise                         → None (the job fails as ``channel_not_configured``)
"""

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.smtp import SmtpConfig, SmtpMailSender
from app.adapters.webex_connect_sms import ConnectSmsConfig, WebexConnectSmsSender
from app.domain.channel_settings import SmsSettings, SmtpSecurity, SmtpSettings
from app.domain.channels import ChannelKind
from app.domain.ports import MessageSender


class ConfiguredSenders:
    """Finds the sender for a channel of an organisation.

    Args:
        uow: Reads channel settings and secrets (needs the settings key for secrets).
        http: Shared HTTP client for the SMS gateway.
        fallback_mail: SMTP from the environment for organisations without their own.
    """

    def __init__(
        self, uow: SqlUnitOfWork, http: httpx.AsyncClient, fallback_mail: SmtpConfig | None
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
            if channel is ChannelKind.EMAIL and self._fallback_mail is not None:
                return SmtpMailSender(self._fallback_mail)
            return None
        if not stored.ready:
            return None
        config = stored.config
        if isinstance(config, SmtpSettings):
            return SmtpMailSender(smtp_config(config, secret))
        return WebexConnectSmsSender(connect_config(config, secret or ""), self._http)


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
    )


def connect_config(settings: SmsSettings, service_key: str) -> ConnectSmsConfig:
    """Return the adapter configuration for an organisation's Connect settings."""
    return ConnectSmsConfig(
        api_base_url=settings.api_base_url,
        service_key=service_key,
        sender_id=settings.sender_id,
        country_code=settings.country_code,
    )
