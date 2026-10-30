"""Channel screens (ADR 0022): SMTP and SMS per organisation, write-only secrets, audit."""

import smtplib
from email.message import EmailMessage
from typing import Any

import httpx
import pytest

from app.adapters import smtp as smtp_module
from app.admin.channel_routes import (
    REASON_LABELS,
    RECORD_LABELS,
    SMS_PROVIDER_LABELS,
    VERDICT_LABELS,
    VERDICT_TONES,
)
from app.domain.channel_settings import SmsProvider
from app.domain.mail_domain import REASONS, Record, Verdict
from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events

PAGE = "/admin/organisations/demo/channels"
SMTP = {
    "smtp_host": "smtp.demo.nl",
    "smtp_port": "587",
    "smtp_security": "starttls",
    "smtp_username": "afspraak",
    "smtp_credential": "Geheim-wachtwoord-123",  # gitleaks:allow (test value)
    "sender_address": "afspraak@demo.nl",
    "sender_name": "Gemeente Demo",
}
SMS = {
    "sms_provider": "webex_connect",
    "sms_api_base_url": "https://api.us.webexconnect.io",
    "sms_api_key": "service-key-abc",
    "sms_sender_id": "GemDemo",
    "sms_country_code": "31",
}


async def _post(site: Site, path: str, data: dict[str, str]) -> httpx.Response:
    return await site.client.post(f"{PAGE}/{path}", data=data, headers=SAME_ORIGIN)


async def test_integrations_page_links_every_organisation(site: Site) -> None:
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/integrations")).text

    assert f'href="{PAGE}"' in page
    assert 'href="/admin/organisations/andere/channels"' in page
    assert "Niet ingesteld" in page


async def test_smtp_is_saved_and_the_password_never_shown(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email", SMTP)

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(PAGE)).text
    assert 'value="smtp.demo.nl"' in page
    assert "Geheim-wachtwoord-123" not in page
    assert "Er is een geheim opgeslagen" in page
    assert 'autocomplete="new-password"' in page
    assert ("sub-super", "integrations.manage", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]
    listing = (await site.client.get("/admin/integrations")).text
    assert "Ingesteld" in listing

    # A blank password keeps the stored one; nothing else changed.
    again = await _post(site, "email", {**SMTP, "smtp_credential": ""})
    assert again.headers["location"] == f"{PAGE}?done=unchanged"


async def test_a_header_injection_in_the_sender_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email", {**SMTP, "sender_name": "Demo\r\nBcc: x@evil.nl"})

    assert response.status_code == 422
    assert 'href="#sender_name"' in response.text
    assert "Geheim-wachtwoord-123" not in response.text  # never echoed, not even on error


async def test_a_login_without_password_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email", {**SMTP, "smtp_credential": ""})

    assert response.status_code == 422
    assert 'href="#smtp_credential"' in response.text


async def test_sms_service_is_saved_and_removed(site: Site) -> None:
    await site.login_as(SUPER)

    saved = await _post(site, "sms", SMS)
    assert saved.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(PAGE)).text
    assert 'value="GemDemo"' in page
    assert "service-key-abc" not in page

    removed = await site.client.post(f"{PAGE}/sms/remove", headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{PAGE}?done=removed"
    assert 'value="GemDemo"' not in (await site.client.get(PAGE)).text


async def test_an_http_gateway_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "sms", {**SMS, "sms_api_base_url": "http://gateway.example"})

    assert response.status_code == 422
    assert 'href="#sms_api_base_url"' in response.text


async def test_an_unknown_sms_provider_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "sms", {**SMS, "sms_provider": "unknown"})

    assert response.status_code == 422
    assert 'href="#sms_provider"' in response.text


def test_every_sms_provider_has_a_label() -> None:
    assert set(SMS_PROVIDER_LABELS) == set(SmsProvider)


class FakeSMTP:
    """Stands in for ``smtplib.SMTP``; ``refuse_login`` makes the server reject the login."""

    sent: list[EmailMessage] = []
    calls: list[str] = []
    refuse_login = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        FakeSMTP.calls.append(f"connect:{host}:{port}")

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def starttls(self, context: Any) -> None:
        FakeSMTP.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        if FakeSMTP.refuse_login:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials")
        FakeSMTP.calls.append(f"login:{username}")

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.refuse_login = [], [], False
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


TEST_MAIL = {"test_recipient": "beheer@demo.nl"}


async def test_a_test_email_is_sent_with_the_stored_settings(
    site: Site, fake_smtp: type[FakeSMTP]
) -> None:
    await site.login_as(SUPER)
    await _post(site, "email", SMTP)
    page = (await site.client.get(PAGE)).text
    assert 'action="/admin/organisations/demo/channels/email/test"' in page

    response = await _post(site, "email/test", TEST_MAIL)

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=test_sent"
    assert fake_smtp.calls == ["connect:smtp.demo.nl:587", "starttls", "login:afspraak"]
    mail = fake_smtp.sent[0]
    assert mail["To"] == "beheer@demo.nl"
    assert mail["From"] == "Gemeente Demo <afspraak@demo.nl>"
    assert "OpenPlanner" in mail["Subject"]
    assert "Testbericht verstuurd" in (await site.client.get(response.headers["location"])).text
    actions = [b for _, b, _, _ in await audit_events(site)]
    assert actions.count("integrations.manage") == 2  # the save and the test (detail: domain test)


async def test_a_failed_test_email_says_why(site: Site, fake_smtp: type[FakeSMTP]) -> None:
    await site.login_as(SUPER)
    await _post(site, "email", SMTP)
    fake_smtp.refuse_login = True

    response = await _post(site, "email/test", TEST_MAIL)

    assert response.status_code == 422
    assert 'href="#test_recipient"' in response.text
    assert "gebruikersnaam of wachtwoord" in response.text
    assert "bad credentials" not in response.text  # the server's own answer is never shown
    assert "Geheim-wachtwoord-123" not in response.text
    assert 'value="beheer@demo.nl"' in response.text  # the address is kept for a retry
    assert fake_smtp.sent == []


async def test_a_test_email_needs_a_mail_server(site: Site, fake_smtp: type[FakeSMTP]) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email/test", TEST_MAIL)

    assert response.status_code == 422
    assert "Er is geen mailserver ingesteld" in response.text
    assert fake_smtp.calls == []


async def test_a_test_email_needs_a_valid_address(site: Site, fake_smtp: type[FakeSMTP]) -> None:
    await site.login_as(SUPER)
    await _post(site, "email", SMTP)

    response = await _post(site, "email/test", {"test_recipient": "a@b.nl\r\nBcc: x@evil.nl"})

    assert response.status_code == 422
    assert 'href="#test_recipient"' in response.text
    assert fake_smtp.calls == []


DOMAIN_RECORDS: dict[str, tuple[str, ...] | None] = {
    "demo.nl": ("v=spf1 include:_spf.provider.example -all", "google-site-verification=x"),
    "s1._domainkey.demo.nl": ("v=DKIM1; k=rsa; p=MIGfMA0G",),
    "_dmarc.demo.nl": ("v=DMARC1; p=none; rua=mailto:dmarc@demo.nl",),
}


async def test_the_sender_domain_check_shows_one_verdict_per_record_adr_0073(site: Site) -> None:
    await site.login_as(SUPER)
    await _post(site, "email", {**SMTP, "dkim_selector": "s1", "reply_to_address": "info@demo.nl"})
    site.dns.records = DOMAIN_RECORDS

    response = await _post(site, "email/domain", {})

    assert response.status_code == 200
    page = response.text
    assert site.dns.asked == ["demo.nl", "s1._domainkey.demo.nl", "_dmarc.demo.nl"]
    assert "Resultaat voor demo.nl" in page
    assert page.count("In orde") == 2  # SPF and DKIM
    assert "Aandachtspunt" in page  # DMARC p=none, also the overall chip
    assert "p=none" in page  # explained in words
    assert "v=spf1" not in page  # never the record itself
    assert 'value="info@demo.nl"' in page
    assert 'value="s1"' in page
    assert ("sub-super", "integrations.manage", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]
    assert "smtp.domain_check.warning" in (await site.client.get("/admin/audit")).text


async def test_the_sender_domain_check_needs_a_sender(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email/domain", {})

    assert response.status_code == 422
    assert 'href="#domain_check"' in response.text
    assert "niets te controleren" in response.text
    assert site.dns.asked == []


async def test_a_failed_lookup_is_reported_as_unknown_not_missing(site: Site) -> None:
    await site.login_as(SUPER)
    await _post(site, "email", SMTP)
    site.dns.records = {"demo.nl": None, "_dmarc.demo.nl": ("v=DMARC1; p=reject",)}

    page = (await _post(site, "email/domain", {})).text

    assert "Niet vast te stellen" in page
    assert "Niet gecontroleerd" in page  # DKIM without a selector
    assert "Ontbreekt" not in page


@pytest.mark.parametrize(
    ("field", "value"),
    [("reply_to_address", "geen-adres"), ("dkim_selector", "s 1")],
)
async def test_an_invalid_reply_address_or_selector_is_refused(
    site: Site, field: str, value: str
) -> None:
    await site.login_as(SUPER)

    response = await _post(site, "email", {**SMTP, field: value})

    assert response.status_code == 422
    assert f'href="#{field}"' in response.text


def test_every_verdict_record_and_reason_has_a_label() -> None:
    assert set(VERDICT_TONES) == set(Verdict)
    assert set(VERDICT_LABELS) == set(Verdict)
    assert set(RECORD_LABELS) == set(Record)
    assert set(REASON_LABELS) == REASONS
