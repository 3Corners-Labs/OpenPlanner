"""OrganisationTextService: who may change an organisation's texts, what is stored and audited."""

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.authorization import Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.organisation_text_service import OrganisationTextService
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> OrganisationTextService:
    clock = fake_clock.FixedClock(NOW)
    return OrganisationTextService(uow, clock, AdminService(uow, clock), ("nl", "en"))


async def test_an_organisation_starts_without_texts(service: OrganisationTextService) -> None:
    view = await service.view(ORG_ADMIN, "demo")

    assert (view.organisation.slug, view.texts) == ("demo", {})


async def test_organisation_admin_saves_texts_and_it_is_audited_with_keys_only(
    service: OrganisationTextService, uow: InMemoryUnitOfWork
) -> None:
    changed = await service.save(
        ORG_ADMIN,
        "demo",
        "nl",
        {"booked.text": " Kijk in uw mail. ", "message.reminder.subject": "Tot snel op {date}"},
    )

    assert changed == ["booked.text", "message.reminder.subject"]
    assert uow.state.organisation_texts[1] == {
        ("nl", "booked.text"): "Kijk in uw mail.",
        ("nl", "message.reminder.subject"): "Tot snel op {date}",
    }
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation, event.group) == (
        "texts.organisation.manage",
        audit.Outcome.SUCCESS,
        "demo",
        None,
    )
    assert event.detail == "texts.nl:booked.text,message.reminder.subject"  # keys, never the text


async def test_none_brings_the_default_back_and_unchanged_texts_are_not_audited(
    service: OrganisationTextService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(ORG_ADMIN, "demo", "nl", {"booked.text": "Eigen tekst"})
    audits = len(uow.state.audit)

    assert await service.save(ORG_ADMIN, "demo", "nl", {"booked.text": "Eigen tekst"}) == []
    assert len(uow.state.audit) == audits
    assert await service.save(ORG_ADMIN, "demo", "nl", {"booked.text": None}) == ["booked.text"]
    assert uow.state.organisation_texts[1] == {}


async def test_a_form_is_saved_whole_or_not_at_all(
    service: OrganisationTextService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as error:
        await service.save(
            ORG_ADMIN, "demo", "nl", {"booked.text": "goed", "confirm.text": "{first_name}"}
        )

    assert (error.value.code, error.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "confirm.text",
    )
    assert uow.state.organisation_texts == {}


@pytest.mark.parametrize(
    ("locale", "key"), [("xx", "booked.text"), ("nl", "admin.title"), ("nl", "home.title")]
)
async def test_unknown_locale_and_keys_off_the_allow_list_are_refused(
    service: OrganisationTextService, locale: str, key: str
) -> None:
    with pytest.raises(DomainError) as error:
        await service.save(ORG_ADMIN, "demo", locale, {key: "x"})

    assert error.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_removing_a_key_off_the_allow_list_is_refused_too(
    service: OrganisationTextService,
) -> None:
    with pytest.raises(DomainError):
        await service.save(ORG_ADMIN, "demo", "nl", {"admin.title": None})


async def test_the_superuser_may_edit_every_organisation(
    service: OrganisationTextService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(SUPER, "andere", "en", {"booked.title": "Confirm"})

    assert uow.state.organisation_texts[2] == {("en", "booked.title"): "Confirm"}


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OUTSIDER])
async def test_others_are_refused_and_the_refusal_is_audited(
    service: OrganisationTextService, uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    with pytest.raises(DomainError) as error:
        await service.save(principal, "demo", "nl", {"booked.text": "x"})
    with pytest.raises(DomainError):
        await service.view(principal, "demo")

    assert error.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.organisation_texts == {}
    assert uow.state.audit[-1].outcome is audit.Outcome.DENIED


async def test_unknown_organisation_is_not_found(service: OrganisationTextService) -> None:
    with pytest.raises(DomainError) as error:
        await service.view(SUPER, "bestaat-niet")

    assert error.value.code is ErrorCode.ORGANISATION_NOT_FOUND


async def test_the_change_callback_runs_only_after_a_real_change(
    uow: InMemoryUnitOfWork,
) -> None:
    calls: list[str] = []
    clock = fake_clock.FixedClock(NOW)
    service = OrganisationTextService(
        uow, clock, AdminService(uow, clock), ("nl",), on_change=lambda: calls.append("changed")
    )

    await service.save(ORG_ADMIN, "demo", "nl", {"booked.text": "Eigen"})
    await service.save(ORG_ADMIN, "demo", "nl", {"booked.text": "Eigen"})

    assert calls == ["changed"]
