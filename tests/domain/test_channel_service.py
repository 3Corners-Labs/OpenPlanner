"""ChannelSettingsService: only the superuser configures channels; secrets stay write-only."""

from dataclasses import replace

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.channel_service import ChannelSettingsService
from app.domain.channel_settings import (
    ChannelConfig,
    SmsProvider,
    SmsSettings,
    SmtpSecurity,
    SmtpSettings,
)
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.mail_domain import Verdict
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.dns import FakeTxtResolver
from tests.fakes.messaging import RecordingSender, RefusingSender
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import DEMO_ORG_ADMIN, DEMO_SUPERUSER, NOW

SUPER, ORG_ADMIN = DEMO_SUPERUSER, DEMO_ORG_ADMIN
SMTP = SmtpSettings("smtp.demo.nl", 587, SmtpSecurity.STARTTLS, "afspraak", "afspraak@demo.nl")
SMS = SmsSettings(SmsProvider.WEBEX_CONNECT, "https://api.us.webexconnect.io", "GemDemo")
TEST_RECIPIENT = "beheer@demo.nl"


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo"}
    state.organisations = {1: Organisation(1, "Demo-gemeente", "demo")}
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> ChannelSettingsService:
    clock = FixedClock(NOW)
    return ChannelSettingsService(uow, clock, AdminService(uow, clock))


async def test_nothing_is_configured_at_first(service: ChannelSettingsService) -> None:
    overview = await service.overview(SUPER, "demo")
    assert overview.organisation.slug == "demo"
    assert overview.channels == {ChannelKind.EMAIL: None, ChannelKind.SMS: None}
    assert [o.slug for o in await service.organisations(SUPER)] == ["demo"]


async def test_saving_smtp_with_password_is_audited_without_values(
    service: ChannelSettingsService, uow: InMemoryUnitOfWork
) -> None:
    changed = await service.save(SUPER, "demo", SMTP, "geheim")

    assert "host" in changed
    assert "secret" in changed
    stored = (await service.overview(SUPER, "demo")).channels[ChannelKind.EMAIL]
    assert stored is not None
    assert stored.secret_set
    assert stored.ready
    assert stored.updated_at == NOW
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation) == (
        "integrations.manage",
        Outcome.SUCCESS,
        "demo",
    )
    assert "smtp.host" in event.detail
    assert "smtp.secret" in event.detail
    assert "geheim" not in event.detail
    assert "smtp.demo.nl" not in event.detail


async def test_saving_again_keeps_the_secret_and_audits_only_changes(
    service: ChannelSettingsService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(SUPER, "demo", SMTP, "geheim")
    events = len(uow.state.audit)

    assert await service.save(SUPER, "demo", SMTP, None) == []
    assert len(uow.state.audit) == events

    other = SmtpSettings("smtp.demo.nl", 465, SmtpSecurity.TLS, "afspraak", "afspraak@demo.nl")
    assert await service.save(SUPER, "demo", other, None) == ["port", "security"]
    assert uow.state.channel_secrets[(1, ChannelKind.EMAIL)] == "geheim"
    assert uow.state.audit[-1].detail == "smtp.port,smtp.security"


async def test_a_login_needs_a_password(service: ChannelSettingsService) -> None:
    with pytest.raises(DomainError) as error:
        await service.save(SUPER, "demo", SMTP, None)
    assert (error.value.code, error.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "smtp_credential",
    )


async def test_smtp_without_login_needs_no_password(service: ChannelSettingsService) -> None:
    relay = SmtpSettings("mailpit", 1025, SmtpSecurity.NONE, None, "noreply@demo.nl")
    await service.save(SUPER, "demo", relay, None)
    stored = (await service.overview(SUPER, "demo")).channels[ChannelKind.EMAIL]
    assert stored is not None
    assert stored.ready
    assert not stored.secret_set


async def test_sms_needs_an_api_key(service: ChannelSettingsService) -> None:
    with pytest.raises(DomainError) as error:
        await service.save(SUPER, "demo", SMS, None)
    assert error.value.field == "sms_api_key"

    await service.save(SUPER, "demo", SMS, "service-key")
    stored = (await service.overview(SUPER, "demo")).channels[ChannelKind.SMS]
    assert stored is not None
    assert stored.ready


@pytest.mark.parametrize(
    ("first", "same_host", "moved", "field"),
    [
        (
            SMS,
            replace(SMS, sender_id="Ander"),
            replace(SMS, api_base_url="https://sms.elsewhere.example"),
            "sms_api_key",
        ),
        (
            SMTP,
            replace(SMTP, sender_name="Ander"),
            replace(SMTP, host="smtp.elsewhere.example"),
            "smtp_credential",
        ),
    ],
)
async def test_a_stored_secret_never_follows_a_new_host(
    service: ChannelSettingsService,
    first: ChannelConfig,
    same_host: ChannelConfig,
    moved: ChannelConfig,
    field: str,
) -> None:
    await service.save(SUPER, "demo", first, "secret-1")
    assert await service.save(SUPER, "demo", same_host, None)  # the stored secret stays

    with pytest.raises(DomainError) as error:
        await service.save(SUPER, "demo", moved, None)
    assert error.value.field == field
    assert await service.save(SUPER, "demo", moved, "secret-2")


async def test_an_invalid_secret_is_refused_before_anything_is_stored(
    service: ChannelSettingsService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError):
        await service.save(SUPER, "demo", SMS, "  ")
    assert uow.state.channel_settings == {}


async def test_remove(service: ChannelSettingsService, uow: InMemoryUnitOfWork) -> None:
    await service.save(SUPER, "demo", SMS, "service-key")
    await service.remove(SUPER, "demo", ChannelKind.SMS)

    assert (await service.overview(SUPER, "demo")).channels[ChannelKind.SMS] is None
    assert (1, ChannelKind.SMS) not in uow.state.channel_secrets
    assert uow.state.audit[-1].detail == "sms.remove"

    with pytest.raises(DomainError) as error:
        await service.remove(SUPER, "demo", ChannelKind.SMS)
    assert error.value.code is ErrorCode.CHANNEL_NOT_CONFIGURED


async def _as_org_admin(service: ChannelSettingsService, action: str) -> None:
    if action == "overview":
        await service.overview(ORG_ADMIN, "demo")
    elif action == "save":
        await service.save(ORG_ADMIN, "demo", SMS, "service-key")
    elif action == "test":
        await service.send_test_email(ORG_ADMIN, "demo", TEST_RECIPIENT, "Test", "Tekst")
    else:
        await service.remove(ORG_ADMIN, "demo", ChannelKind.SMS)


@pytest.mark.parametrize("action", ["overview", "save", "remove", "test"])
async def test_org_admin_may_not_configure_channels_handover_section_6(
    service: ChannelSettingsService, uow: InMemoryUnitOfWork, action: str
) -> None:
    with pytest.raises(DomainError) as error:
        await _as_org_admin(service, action)
    assert error.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.audit[-1].outcome is Outcome.DENIED
    assert uow.state.channel_settings == {}
    assert await service.organisations(ORG_ADMIN) == []


async def test_unknown_organisation(service: ChannelSettingsService) -> None:
    with pytest.raises(DomainError) as error:
        await service.overview(SUPER, "nope")
    assert error.value.code is ErrorCode.ORGANISATION_NOT_FOUND


def _with_senders(uow: InMemoryUnitOfWork, senders: object) -> ChannelSettingsService:
    clock = FixedClock(NOW)
    return ChannelSettingsService(uow, clock, AdminService(uow, clock), senders)  # type: ignore[arg-type]


async def test_a_test_email_goes_through_the_organisations_sender_and_is_audited(
    uow: InMemoryUnitOfWork,
) -> None:
    sender = RecordingSender()
    service = _with_senders(uow, sender)

    failure = await service.send_test_email(SUPER, "demo", TEST_RECIPIENT, "Test", "Tekst")

    assert failure is None
    assert sender.asked == [(1, ChannelKind.EMAIL)]
    message = sender.sent[0]
    assert (message.channel, message.recipient, message.subject, message.text) == (
        ChannelKind.EMAIL,
        TEST_RECIPIENT,
        "Test",
        "Tekst",
    )
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.detail) == (
        "integrations.manage",
        Outcome.SUCCESS,
        "smtp.test.ok",
    )
    assert TEST_RECIPIENT not in repr(uow.state.audit)


async def test_a_failed_test_email_returns_and_audits_only_the_code(
    uow: InMemoryUnitOfWork,
) -> None:
    service = _with_senders(uow, RefusingSender("unauthorized"))

    failure = await service.send_test_email(SUPER, "demo", TEST_RECIPIENT, "Test", "Tekst")

    assert failure == "unauthorized"
    assert uow.state.audit[-1].detail == "smtp.test.unauthorized"


async def test_a_test_email_needs_a_mail_server(uow: InMemoryUnitOfWork) -> None:
    service = _with_senders(uow, RecordingSender(unconfigured=frozenset({ChannelKind.EMAIL})))

    with pytest.raises(DomainError) as error:
        await service.send_test_email(SUPER, "demo", TEST_RECIPIENT, "Test", "Tekst")

    assert error.value.code is ErrorCode.CHANNEL_NOT_CONFIGURED


@pytest.mark.parametrize("recipient", ["", "geen-adres", "a@b.nl\r\nBcc: x@evil.nl", "a@b"])
async def test_a_test_email_needs_a_valid_recipient(
    uow: InMemoryUnitOfWork, recipient: str
) -> None:
    sender = RecordingSender()
    service = _with_senders(uow, sender)

    with pytest.raises(DomainError) as error:
        await service.send_test_email(SUPER, "demo", recipient, "Test", "Tekst")

    assert (error.value.code, error.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "test_recipient",
    )
    assert sender.sent == []


async def test_without_senders_there_is_nothing_to_test(service: ChannelSettingsService) -> None:
    with pytest.raises(DomainError) as error:
        await service.send_test_email(SUPER, "demo", TEST_RECIPIENT, "Test", "Tekst")

    assert error.value.code is ErrorCode.CHANNEL_NOT_CONFIGURED


RECORDS: dict[str, tuple[str, ...] | None] = {
    "demo.nl": ("v=spf1 include:_spf.provider.example -all",),
    "s1._domainkey.demo.nl": ("v=DKIM1; k=rsa; p=MIGfMA0G",),
    "_dmarc.demo.nl": ("v=DMARC1; p=none",),
    "platform.nl": ("v=spf1 -all",),
}


def _with_resolver(
    uow: InMemoryUnitOfWork, resolver: FakeTxtResolver | None, fallback: str | None = None
) -> ChannelSettingsService:
    clock = FixedClock(NOW)
    return ChannelSettingsService(
        uow, clock, AdminService(uow, clock), None, resolver, lambda: fallback
    )


async def test_the_domain_check_uses_the_stored_sender_and_selector_and_audits_the_finding(
    uow: InMemoryUnitOfWork,
) -> None:
    resolver = FakeTxtResolver(RECORDS)
    service = _with_resolver(uow, resolver, "Platform <noreply@platform.nl>")
    await service.save(SUPER, "demo", replace(SMTP, dkim_selector="s1"), "pw")

    report = await service.check_sender_domain(SUPER, "demo")

    assert resolver.asked == ["demo.nl", "s1._domainkey.demo.nl", "_dmarc.demo.nl"]
    assert report.domain == "demo.nl"
    assert [c.verdict for c in report.checks] == [Verdict.OK, Verdict.OK, Verdict.WARNING]
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.detail) == (
        "integrations.manage",
        Outcome.SUCCESS,
        "smtp.domain_check.warning",
    )
    assert "spf1" not in repr(uow.state.audit)


async def test_without_own_smtp_the_fallback_sender_domain_is_checked_without_dkim(
    uow: InMemoryUnitOfWork,
) -> None:
    resolver = FakeTxtResolver(RECORDS)
    service = _with_resolver(uow, resolver, "Platform <noreply@platform.nl>")

    report = await service.check_sender_domain(SUPER, "demo")

    assert resolver.asked == ["platform.nl", "_dmarc.platform.nl"]
    assert report.domain == "platform.nl"
    assert [c.verdict for c in report.checks] == [
        Verdict.OK,
        Verdict.NOT_CHECKED,
        Verdict.MISSING,
    ]
    assert uow.state.audit[-1].detail == "smtp.domain_check.missing"


async def test_the_domain_check_needs_a_sender_and_a_resolver(uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as no_sender:
        await _with_resolver(uow, FakeTxtResolver()).check_sender_domain(SUPER, "demo")
    assert no_sender.value.code is ErrorCode.CHANNEL_NOT_CONFIGURED

    with pytest.raises(DomainError) as no_resolver:
        await _with_resolver(uow, None, "x@demo.nl").check_sender_domain(SUPER, "demo")
    assert no_resolver.value.code is ErrorCode.CHANNEL_NOT_CONFIGURED


async def test_the_domain_check_is_superuser_only(uow: InMemoryUnitOfWork) -> None:
    resolver = FakeTxtResolver(RECORDS)
    service = _with_resolver(uow, resolver, "x@demo.nl")

    with pytest.raises(DomainError) as error:
        await service.check_sender_domain(ORG_ADMIN, "demo")

    assert error.value.code is ErrorCode.NOT_PERMITTED
    assert resolver.asked == []


RELAY = SmtpSettings("relay.demo.nl", 25, SmtpSecurity.NONE, None, "noreply@demo.nl")


def production_service(uow: InMemoryUnitOfWork, *, allowed: bool = False) -> ChannelSettingsService:
    clock = FixedClock(NOW)
    return ChannelSettingsService(
        uow, clock, AdminService(uow, clock), production=True, plaintext_relay_allowed=allowed
    )


async def test_production_refuses_to_save_smtp_without_tls(uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as error:
        await production_service(uow).save(SUPER, "demo", RELAY, None)
    assert (error.value.code, error.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "smtp_security",
    )
    assert (await production_service(uow).overview(SUPER, "demo")).channels[
        ChannelKind.EMAIL
    ] is None


async def test_production_saves_smtp_without_tls_when_the_host_allows_it(
    uow: InMemoryUnitOfWork,
) -> None:
    await production_service(uow, allowed=True).save(SUPER, "demo", RELAY, None)
    assert await production_service(uow, allowed=True).plaintext_relays(SUPER) == []


async def test_a_stored_plaintext_relay_is_reported_after_an_upgrade(
    uow: InMemoryUnitOfWork, service: ChannelSettingsService
) -> None:
    await service.save(SUPER, "demo", RELAY, None)  # saved before the rule existed
    assert await production_service(uow).plaintext_relays(SUPER) == ["demo"]
    assert await service.plaintext_relays(SUPER) == []  # outside production nothing changes
    assert await production_service(uow, allowed=True).plaintext_relays(SUPER) == []


async def test_an_organisation_admin_sees_no_plaintext_relays(uow: InMemoryUnitOfWork) -> None:
    await production_service(uow, allowed=True).save(SUPER, "demo", RELAY, None)
    assert await production_service(uow).plaintext_relays(ORG_ADMIN) == []
