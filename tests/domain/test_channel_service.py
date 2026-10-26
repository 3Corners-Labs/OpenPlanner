"""ChannelSettingsService: only the superuser configures channels; secrets stay write-only."""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.channel_service import ChannelSettingsService
from app.domain.channel_settings import SmsSettings, SmtpSecurity, SmtpSettings
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
SMTP = SmtpSettings("smtp.demo.nl", 587, SmtpSecurity.STARTTLS, "afspraak", "afspraak@demo.nl")
SMS = SmsSettings("https://api.us.webexconnect.io", "GemDemo")


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


async def test_sms_needs_a_service_key(service: ChannelSettingsService) -> None:
    with pytest.raises(DomainError) as error:
        await service.save(SUPER, "demo", SMS, None)
    assert error.value.field == "sms_service_key"

    await service.save(SUPER, "demo", SMS, "service-key")
    stored = (await service.overview(SUPER, "demo")).channels[ChannelKind.SMS]
    assert stored is not None
    assert stored.ready


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
    else:
        await service.remove(ORG_ADMIN, "demo", ChannelKind.SMS)


@pytest.mark.parametrize("action", ["overview", "save", "remove"])
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
