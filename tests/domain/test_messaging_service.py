"""MessagingService: reminder rules and group texts, authorised and audited per group."""

from datetime import UTC, datetime

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.channel_settings import ChannelSettings, SmsProvider, SmsSettings
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.messaging_service import MAX_REMINDER_RULES, MessagingService
from app.domain.scheduling import Organisation, PhoneMode
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, make_group
from tests.support.principals import principal

ORG_ADMIN = principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"team"}))
OTHER_ORG = principal("o-2", "X", frozenset({Role.ORG_ADMIN}), frozenset({"other"}), frozenset())
SMS_RULE = ReminderRule(ChannelKind.SMS, 120)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo"}
    state.organisations = {1: Organisation(1, "Demo", "demo")}
    state.groups[1] = make_group(id=1, organisation_id=1, slug="team", name="Team")
    state.reminder_rules[1] = [ReminderRule(ChannelKind.EMAIL, 24 * 60)]
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> MessagingService:
    clock = FixedClock(NOW)
    return MessagingService(uow, clock, AdminService(uow, clock), ("nl", "en"))


async def test_reminders_show_rules_and_ready_channels(
    service: MessagingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.channel_settings[(1, ChannelKind.SMS)] = ChannelSettings(
        SmsSettings(SmsProvider.WEBEX_CONNECT, "https://api.us.webexconnect.io", "Demo"),
        secret_set=True,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await service.add_reminder(ORG_ADMIN, "team", ReminderRule(ChannelKind.EMAIL, 60))

    reminders = await service.reminders(ORG_ADMIN, "team")

    assert [(r.channel, r.minutes_before) for r in reminders.rules] == [
        (ChannelKind.EMAIL, 24 * 60),
        (ChannelKind.EMAIL, 60),
    ]
    assert reminders.ready == {ChannelKind.SMS}
    assert reminders.asks_phone


async def test_add_and_remove_an_sms_reminder_is_audited(
    service: MessagingService, uow: InMemoryUnitOfWork
) -> None:
    await service.add_reminder(ORG_ADMIN, "team", SMS_RULE)
    assert SMS_RULE in uow.state.reminder_rules[1]
    event = uow.state.audit[-1]
    assert (event.action, event.group, event.detail) == (
        "reminders.manage",
        "team",
        "reminder.add:sms:120",
    )

    await service.remove_reminder(ORG_ADMIN, "team", ChannelKind.SMS, 120)
    assert SMS_RULE not in uow.state.reminder_rules[1]
    assert uow.state.audit[-1].detail == "reminder.remove:sms:120"


async def test_duplicate_reminder(service: MessagingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.add_reminder(ORG_ADMIN, "team", ReminderRule(ChannelKind.EMAIL, 24 * 60))
    assert error.value.code is ErrorCode.REMINDER_EXISTS


async def test_at_most_five_reminders(service: MessagingService) -> None:
    for minutes in range(60, 60 * MAX_REMINDER_RULES, 60):
        await service.add_reminder(ORG_ADMIN, "team", ReminderRule(ChannelKind.SMS, minutes))
    with pytest.raises(DomainError) as error:
        await service.add_reminder(ORG_ADMIN, "team", ReminderRule(ChannelKind.SMS, 5))
    assert error.value.field == "reminders"


async def test_whatsapp_cannot_be_enabled_adr_0009(service: MessagingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.add_reminder(ORG_ADMIN, "team", ReminderRule(ChannelKind.WHATSAPP, 60))
    assert error.value.field == "channel"


async def test_sms_needs_a_phone_field(service: MessagingService, uow: InMemoryUnitOfWork) -> None:
    uow.state.groups[1] = make_group(
        id=1, organisation_id=1, slug="team", name="Team", phone_mode=PhoneMode.HIDDEN
    )
    with pytest.raises(DomainError) as error:
        await service.add_reminder(ORG_ADMIN, "team", SMS_RULE)
    assert error.value.field == "channel"
    assert not (await service.reminders(ORG_ADMIN, "team")).asks_phone


async def test_removing_an_unknown_reminder(service: MessagingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.remove_reminder(ORG_ADMIN, "team", ChannelKind.SMS, 999)
    assert error.value.code is ErrorCode.REMINDER_NOT_FOUND


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OTHER_ORG])
async def test_reminders_need_reminders_manage_on_the_group(
    service: MessagingService, uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    with pytest.raises(DomainError) as error:
        await service.add_reminder(principal, "team", SMS_RULE)
    assert error.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.audit[-1].outcome is Outcome.DENIED
    assert SMS_RULE not in uow.state.reminder_rules[1]


async def test_group_admin_edits_texts_and_resets_one(
    service: MessagingService, uow: InMemoryUnitOfWork
) -> None:
    changed = await service.save_texts(
        GROUP_ADMIN,
        "team",
        "nl",
        {"message.reminder.subject": " Tot {date}! ", "message.reminder.sms": None},
    )
    assert changed == ["message.reminder.subject"]
    assert await service.texts(GROUP_ADMIN, "team") == {
        ("nl", "message.reminder.subject"): "Tot {date}!"
    }
    assert uow.state.audit[-1].detail == "texts.nl:message.reminder.subject"
    assert "Tot" not in uow.state.audit[-1].detail

    assert await service.save_texts(GROUP_ADMIN, "team", "nl", {"message.reminder.subject": None})
    assert await service.texts(GROUP_ADMIN, "team") == {}


async def test_an_invalid_text_stores_nothing(
    service: MessagingService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as error:
        await service.save_texts(
            GROUP_ADMIN,
            "team",
            "nl",
            {"message.reminder.subject": "Goed", "message.reminder.intro": "{first_name}"},
        )
    assert error.value.field == "message.reminder.intro"
    assert uow.state.group_texts == {}


async def test_unknown_locale(service: MessagingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.save_texts(GROUP_ADMIN, "team", "de", {})
    assert error.value.field == "locale"


async def test_texts_of_another_organisation_are_out_of_reach(
    service: MessagingService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as error:
        await service.save_texts(OTHER_ORG, "team", "nl", {"message.reminder.subject": "X"})
    assert error.value.code is ErrorCode.NOT_PERMITTED
    with pytest.raises(DomainError):
        await service.texts(OTHER_ORG, "team")
    assert uow.state.group_texts == {}
