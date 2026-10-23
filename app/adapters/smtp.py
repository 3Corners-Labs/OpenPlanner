"""SMTP adapter for the ``MessageSender`` port (email channel).

Uses the standard library in a worker thread; no extra dependency. Credentials come from the
environment for now; per-organisation settings follow with the admin UI (ADR 0008).
"""

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.domain.channels import ChannelKind
from app.domain.ports import OutboundMessage

DEFAULT_TIMEOUT_SECONDS = 15.0


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
            smtplib.SMTPException, OSError: On delivery failure (the job retries).
        """
        if message.channel is not ChannelKind.EMAIL:
            raise ValueError("SmtpMailSender only sends email")
        await asyncio.to_thread(self._deliver, build_email(message, self._config.sender))

    def _deliver(self, email: EmailMessage) -> None:
        """Open a connection, secure and authenticate it if configured, and send."""
        config = self._config
        with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as smtp:
            if config.starttls:
                smtp.starttls(context=ssl.create_default_context())
            if config.username and config.password:
                smtp.login(config.username, config.password)
            smtp.send_message(email)
