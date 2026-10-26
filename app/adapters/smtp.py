"""SMTP adapter for the ``MessageSender`` port (email channel).

Uses the standard library in a worker thread; no extra dependency. Each organisation's server
comes from its channel settings in the admin portal (ADR 0022); ``SMTP_HOST`` in the
environment is the fallback for organisations without one (development: Mailpit).

Failures are raised as ``IntegrationError`` with a short code, never the server's answer
(which can name the recipient):

    unreachable        DNS, refused, timeout, dropped connection        retried
    tls_failed         TLS handshake or certificate
    not_supported      server lacks STARTTLS or AUTH
    unauthorized       login refused
    sender_refused     MAIL FROM refused
    recipient_refused  every recipient refused
    rejected_<status>  any other SMTP reply                             retried if 4xx
    server_error       any other SMTP failure                           retried
"""

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.domain.channels import ChannelKind
from app.domain.ports import IntegrationError, OutboundMessage

DEFAULT_TIMEOUT_SECONDS = 15.0
TRANSIENT, PERMANENT = 400, 500
"""SMTP reply classes: 4xx may succeed later, 5xx will not."""


@dataclass(frozen=True)
class SmtpConfig:
    """Where and how to deliver mail."""

    host: str
    port: int
    sender: str
    starttls: bool = False
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    implicit_tls: bool = False


def build_email(message: OutboundMessage, sender: str) -> EmailMessage:
    """Return the MIME message: plain text, optional HTML alternative, attachments."""
    email = EmailMessage()
    email["From"] = sender
    email["To"] = message.recipient
    email["Subject"] = message.subject
    email["Date"] = formatdate(localtime=False)
    email["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].strip("> "))
    email.set_content(message.text)
    if message.html is not None:
        email.add_alternative(message.html, subtype="html")
    for attachment in message.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        email.add_attachment(
            attachment.data, maintype=maintype, subtype=subtype, filename=attachment.filename
        )
    return email


class SmtpMailSender:
    """Sends email messages over SMTP, optionally with STARTTLS and login."""

    def __init__(self, config: SmtpConfig) -> None:
        """Store the configuration."""
        self._config = config

    async def send(self, message: OutboundMessage) -> None:
        """Deliver an email message.

        Raises:
            ValueError: If the message is not for the email channel.
            IntegrationError: On delivery failure; ``retryable`` says whether a retry can help.
        """
        if message.channel is not ChannelKind.EMAIL:
            raise ValueError("SmtpMailSender only sends email")
        try:
            await asyncio.to_thread(self._deliver, build_email(message, self._config.sender))
        except (smtplib.SMTPException, OSError) as error:
            raise classify(error) from None  # the server's answer may name the recipient

    def _deliver(self, email: EmailMessage) -> None:
        """Open a connection, secure and authenticate it if configured, and send."""
        config = self._config
        context = ssl.create_default_context()
        connection = (
            smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout, context=context)
            if config.implicit_tls
            else smtplib.SMTP(config.host, config.port, timeout=config.timeout)
        )
        with connection as smtp:
            if config.starttls and not config.implicit_tls:
                smtp.starttls(context=context)
            if config.username and config.password:
                smtp.login(config.username, config.password)
            smtp.send_message(email)


def classify(error: smtplib.SMTPException | OSError) -> IntegrationError:
    """Return the ``IntegrationError`` for an SMTP failure (see the module docstring).

    The order matters: ``SMTPException`` is an ``OSError`` and ``SSLError`` too.
    """
    if isinstance(error, ssl.SSLError):
        return IntegrationError("tls_failed", retryable=False)
    if isinstance(error, smtplib.SMTPNotSupportedError):
        return IntegrationError("not_supported", retryable=False)
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return IntegrationError("unauthorized", retryable=False)
    if isinstance(error, smtplib.SMTPSenderRefused):
        return IntegrationError("sender_refused", retryable=False)
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return IntegrationError("recipient_refused", retryable=False)
    if isinstance(error, smtplib.SMTPServerDisconnected | smtplib.SMTPConnectError):
        return IntegrationError("unreachable", retryable=True)
    if isinstance(error, smtplib.SMTPResponseException):
        status = error.smtp_code
        return IntegrationError(f"rejected_{status}", retryable=TRANSIENT <= status < PERMANENT)
    if isinstance(error, smtplib.SMTPException):
        return IntegrationError("server_error", retryable=True)
    return IntegrationError("unreachable", retryable=True)
