import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Any

import pytest

from app.adapters import smtp as smtp_module
from app.adapters.smtp import SmtpConfig, SmtpMailSender, build_email
from app.domain.channels import ChannelKind
from app.domain.ports import Attachment, IntegrationError, OutboundMessage

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


def test_every_message_is_marked_auto_submitted_and_reply_to_only_when_given_adr_0073() -> None:
    without = build_email(MESSAGE, "Team <noreply@example.nl>")
    assert without["Auto-Submitted"] == "auto-generated"
    assert without["Reply-To"] is None

    with_reply = build_email(MESSAGE, "Team <noreply@example.nl>", "afspraken@example.nl")
    assert with_reply["Reply-To"] == "afspraken@example.nl"
    assert with_reply["Auto-Submitted"] == "auto-generated"


async def test_the_configured_reply_address_reaches_the_message() -> None:
    config = SmtpConfig("mailpit", 1025, "noreply@example.nl", reply_to="afspraken@example.nl")

    await SmtpMailSender(config).send(MESSAGE)

    assert FakeSMTP.instances[0].sent[0]["Reply-To"] == "afspraken@example.nl"


class FakeSMTPSSL(FakeSMTP):
    def __init__(self, host: str, port: int, timeout: float, context: Any) -> None:
        super().__init__(host, port, timeout)
        self.calls.append("tls")


async def test_implicit_tls_connects_securely_and_skips_starttls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtp_module.smtplib, "SMTP_SSL", FakeSMTPSSL)
    test_pw = "secret"  # gitleaks:allow (test value)
    config = SmtpConfig("smtp", 465, "noreply@example.nl", True, "user", test_pw, implicit_tls=True)

    await SmtpMailSender(config).send(MESSAGE)

    assert FakeSMTP.instances[0].calls == ["tls", "login:user", "quit"]


FAILURES: list[tuple[str, Exception, str, bool]] = [
    ("connect", ConnectionRefusedError(61, "refused"), "unreachable", True),
    ("connect", socket.gaierror(8, "unknown host"), "unreachable", True),
    ("connect", TimeoutError("timed out"), "unreachable", True),
    ("connect", smtplib.SMTPServerDisconnected("closed"), "unreachable", True),
    ("starttls", ssl.SSLCertVerificationError("bad certificate"), "tls_failed", False),
    ("starttls", smtplib.SMTPNotSupportedError("no STARTTLS"), "not_supported", False),
    ("login", smtplib.SMTPAuthenticationError(535, b"bad login"), "unauthorized", False),
    ("send", smtplib.SMTPSenderRefused(553, b"no", "noreply@example.nl"), "sender_refused", False),
    (
        "send",
        smtplib.SMTPRecipientsRefused({"ada@example.org": (550, b"unknown user")}),
        "recipient_refused",
        False,
    ),
    ("send", smtplib.SMTPDataError(451, b"try later"), "rejected_451", True),
    ("send", smtplib.SMTPDataError(554, b"spam"), "rejected_554", False),
    ("send", smtplib.SMTPException("odd"), "server_error", True),
]


@pytest.mark.parametrize(
    ("step", "failure", "code", "retryable"), FAILURES, ids=[f[2] for f in FAILURES]
)
async def test_failures_are_reported_as_codes_without_details(
    monkeypatch: pytest.MonkeyPatch, step: str, failure: Exception, code: str, retryable: bool
) -> None:
    class FailingSMTP(FakeSMTP):
        def __init__(self, host: str, port: int, timeout: float) -> None:
            if step == "connect":
                raise failure
            super().__init__(host, port, timeout)

        def starttls(self, context: Any) -> None:
            if step == "starttls":
                raise failure

        def login(self, username: str, password: str) -> None:
            if step == "login":
                raise failure

        def send_message(self, message: EmailMessage) -> None:
            if step == "send":
                raise failure

    monkeypatch.setattr(smtp_module.smtplib, "SMTP", FailingSMTP)
    config = SmtpConfig("smtp", 587, "noreply@example.nl", True, "user", "pw")

    with pytest.raises(IntegrationError) as error:
        await SmtpMailSender(config).send(MESSAGE)

    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__  # the server's answer may name the recipient
    assert "ada@example.org" not in str(error.value)
