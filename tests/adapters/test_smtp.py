from email.message import EmailMessage
from typing import Any

import pytest

from app.adapters import smtp as smtp_module
from app.adapters.smtp import SmtpConfig, SmtpMailSender, build_email
from app.domain.channels import ChannelKind
from app.domain.ports import Attachment, OutboundMessage

MESSAGE = OutboundMessage(
    channel=ChannelKind.EMAIL,
    recipient="ada@example.org",
    subject="Onderwerp",
    text="Tekst",
    html="<p>Tekst</p>",
    attachments=(Attachment("afspraak.ics", "text/calendar", b"BEGIN:VCALENDAR"),),
)


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        self.calls.append("quit")

    def starttls(self, context: Any) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append(f"login:{username}")

    def send_message(self, message: EmailMessage) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", FakeSMTP)


async def test_sends_multipart_mail_with_attachment() -> None:
    await SmtpMailSender(SmtpConfig("mailpit", 1025, "Team <noreply@example.nl>")).send(MESSAGE)

    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("mailpit", 1025)
    assert smtp.calls == ["quit"]
    email = smtp.sent[0]
    assert email["To"] == "ada@example.org"
    assert email["Message-ID"].endswith("@example.nl>")
    parts = [part.get_content_type() for part in email.walk()]
    assert {"text/plain", "text/html", "text/calendar"} <= set(parts)


async def test_starttls_and_login_when_configured() -> None:
    config = SmtpConfig("smtp", 587, "noreply@example.nl", True, "user", "secret")

    await SmtpMailSender(config).send(MESSAGE)

    assert FakeSMTP.instances[0].calls == ["starttls", "login:user", "quit"]
    assert "secret" not in repr(config)


async def test_only_email_is_accepted() -> None:
    sms = OutboundMessage(channel=ChannelKind.SMS, recipient="+316", subject="", text="x")

    with pytest.raises(ValueError, match="email"):
        await SmtpMailSender(SmtpConfig("smtp", 25, "noreply@example.nl")).send(sms)


def test_plain_text_only_message() -> None:
    plain = OutboundMessage(channel=ChannelKind.EMAIL, recipient="a@b.nl", subject="s", text="t")

    email = build_email(plain, "noreply@example.nl")

    assert email.get_content_type() == "text/plain"


class FakeSMTPSSL(FakeSMTP):
    def __init__(self, host: str, port: int, timeout: float, context: Any) -> None:
        super().__init__(host, port, timeout)
        self.calls.append("tls")


async def test_implicit_tls_connects_securely_and_skips_starttls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtp_module.smtplib, "SMTP_SSL", FakeSMTPSSL)
    config = SmtpConfig(
        "smtp", 465, "noreply@example.nl", True, "user", "secret", implicit_tls=True
    )

    await SmtpMailSender(config).send(MESSAGE)

    assert FakeSMTP.instances[0].calls == ["tls", "login:user", "quit"]
