"""The readiness checklist of a group and an organisation (ADR 0047 §5)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.branding import Branding
from app.domain.channel_settings import ChannelSettings, SmtpSecurity, SmtpSettings
from app.domain.checks import CheckStatus, Finding, GroupCheck
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.errors import DomainError, ErrorCode
from app.domain.readiness import (
    Item,
    ReadinessService,
    State,
    group_checklist,
    has_staffing,
    organisation_checklist,
)
from app.domain.scheduling import ScheduleRule
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW, make_group, window

TODAY = NOW.date()
SMTP = SmtpSettings(
    host="smtp.voorbeeld.nl",
    port=587,
    security=SmtpSecurity.STARTTLS,
    username=None,
    sender_address="afspraak@voorbeeld.nl",
    sender_name="Acme",
)
"""An SMTP server without a login: ready to send without a stored secret."""


def _check(status: CheckStatus) -> GroupCheck:
    return GroupCheck(1, NOW, (Finding("webex", "webex.host", status),))


def _states(lines: object) -> dict[Item, State]:
    return {line.item: line.state for line in lines}  # type: ignore[attr-defined]


def test_staffing_counts_only_rules_that_still_apply() -> None:
    ended = ScheduleRule(0, window(), valid_to=TODAY - timedelta(days=1))
    open_ended = ScheduleRule(0, window())
    until_today = ScheduleRule(0, window(), valid_to=TODAY)

    assert not has_staffing([], TODAY)
    assert not has_staffing([ended], TODAY)
    assert has_staffing([ended, open_ended], TODAY)
    assert has_staffing([until_today], TODAY)


def test_a_group_without_a_platform_needs_no_check() -> None:
    group = make_group()

    checklist = group_checklist(group, staffed=True, own_texts=False, sends=True, check=None)

    assert _states(checklist.lines) == {
        Item.STAFFING: State.DONE,
        Item.TEXTS: State.DONE,
        Item.CHANNELS: State.DONE,
        Item.CHECK: State.NOT_NEEDED,
    }
    assert checklist.ready
    assert checklist.missing == 0


@pytest.mark.parametrize(
    ("check", "state"),
    [
        (None, State.MISSING),
        (_check(CheckStatus.FAIL), State.MISSING),
        (_check(CheckStatus.WARN), State.DONE),
        (_check(CheckStatus.OK), State.DONE),
    ],
)
def test_a_group_that_calls_a_platform_needs_a_check_that_did_not_fail(
    check: GroupCheck | None, state: State
) -> None:
    group = make_group(video_host_email="host@voorbeeld.nl")

    checklist = group_checklist(group, staffed=True, own_texts=True, sends=True, check=check)

    assert _states(checklist.lines)[Item.CHECK] is state


def test_missing_staffing_and_channels_are_counted() -> None:
    group = make_group()

    checklist = group_checklist(group, staffed=False, own_texts=True, sends=False, check=None)

    assert not checklist.ready
    assert checklist.missing == 2
    texts = next(line for line in checklist.lines if line.item is Item.TEXTS)
    assert texts.own


def test_an_organisation_needs_channels_its_own_branding_and_contact() -> None:
    bare = organisation_checklist(sends=False, branding=Branding(), contact=False)
    done = organisation_checklist(sends=True, branding=Branding(display_name="Acme"), contact=True)

    assert [line.item for line in bare.lines] == [Item.CHANNELS, Item.BRANDING, Item.CONTACT]
    assert bare.missing == 3
    assert done.ready


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> ReadinessService:
    clock = FixedClock(NOW)
    return ReadinessService(uow, clock, AdminService(uow, clock))


async def test_a_new_group_misses_staffing_and_channels(service: ReadinessService) -> None:
    checklist = await service.group(GROUP_ADMIN, "demo")

    assert _states(checklist.lines) == {
        Item.STAFFING: State.MISSING,
        Item.TEXTS: State.DONE,
        Item.CHANNELS: State.MISSING,
        Item.CHECK: State.NOT_NEEDED,
    }


async def test_the_service_reads_rules_texts_and_channels(
    service: ReadinessService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.rules[1] = [ScheduleRule(0, window())]
    uow.state.group_texts[1] = {("nl", "confirmation.intro"): "Welkom"}
    email = ChannelSettings(SMTP, secret_set=False, updated_at=NOW)
    uow.state.channel_settings[(1, email.channel)] = email

    checklist = await service.group(SUPER, "demo")

    assert checklist.ready
    assert next(line for line in checklist.lines if line.item is Item.TEXTS).own


async def test_the_organisation_checklist_reads_branding_and_contact(
    service: ReadinessService, uow: InMemoryUnitOfWork
) -> None:
    organisation = uow.state.organisations[1]
    uow.state.organisations[1] = replace(organisation, branding=Branding(display_name="Acme"))
    option = ContactOption.create(ContactKind.PHONE, "14 020")
    uow.state.contact_blocks[1] = ContactBlock((option,), {})

    checklist = await service.organisation(ORG_ADMIN, "demo")

    assert _states(checklist.lines) == {
        Item.CHANNELS: State.MISSING,
        Item.BRANDING: State.DONE,
        Item.CONTACT: State.DONE,
    }


async def test_outsiders_and_unknown_objects_are_refused(service: ReadinessService) -> None:
    with pytest.raises(DomainError) as group_refused:
        await service.group(OUTSIDER, "demo")
    with pytest.raises(DomainError) as org_refused:
        await service.organisation(GROUP_ADMIN, "andere")
    with pytest.raises(DomainError) as unknown:
        await service.group(SUPER, "nope")

    assert group_refused.value.code is ErrorCode.NOT_PERMITTED
    assert org_refused.value.code is ErrorCode.NOT_PERMITTED
    assert unknown.value.code in {ErrorCode.GROUP_NOT_FOUND, ErrorCode.NOT_PERMITTED}
