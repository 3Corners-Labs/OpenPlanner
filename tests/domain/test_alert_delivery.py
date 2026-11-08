"""Alerts delivered to email addresses and Webex spaces (ADR 0082 points 9 and 10)."""

from datetime import timedelta

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.alert_delivery import (
    DELIVERY_FAILED,
    MAX_TARGETS,
    RETRY_AFTER,
    AlertNotifier,
    AlertTargetService,
    after_failure,
    due,
)
from app.domain.alert_model import AlertChannelKind, AlertTarget, DeliveryState, RenderedAlert
from app.domain.alerts import Alert, AlertLevel
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.connection_health import HEALTH_EVENT
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import IntegrationError
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeAlertSenders, FakeBotTokens
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW

REPEAT = timedelta(hours=4)


def compose(alert: Alert, target: AlertTarget, suppressed: int, locale: str) -> RenderedAlert:
    text = f"{alert.action}|{alert.reason}|{alert.organisation}|{suppressed}|{locale}"
    return RenderedAlert(alert.action, text, text)


def down(org: str | None = "demo", at: timedelta = timedelta()) -> AuditEvent:
    return AuditEvent(
        NOW + at,
        SYSTEM_ACTOR,
        HEALTH_EVENT,
        Outcome.FAILURE,
        "down",
        org,
        None,
        "platform:wxcc,from:ok,to:down",
        Source.SYSTEM,
    )


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def target(
    org_id: int | None = 1, kind: AlertChannelKind = AlertChannelKind.EMAIL, level: str = "warning"
) -> AlertTarget:
    address = (
        "ops@voorbeeld.nl" if kind is AlertChannelKind.EMAIL else "Y2lzY29zcGFyazovL3VzL1JPT00v"
    )
    return AlertTarget(None, org_id, kind, address, level)


def test_a_target_needs_a_valid_address_and_level() -> None:
    with pytest.raises(DomainError):
        AlertTarget(None, 1, AlertChannelKind.EMAIL, "not an address", "warning")
    with pytest.raises(DomainError):
        AlertTarget(None, 1, AlertChannelKind.WEBEX_SPACE, "short", "warning")
    with pytest.raises(DomainError):
        AlertTarget(None, 1, AlertChannelKind.EMAIL, "ops@voorbeeld.nl", "info")


def test_due_sends_a_new_streak_and_repeats_only_after_the_interval() -> None:
    alert = Alert(HEALTH_EVENT, AlertLevel.OUTAGE, "down", "demo", None, "", NOW, NOW)
    assert due(alert, None, NOW, REPEAT)
    sent = DeliveryState(1, alert.key, alert.token, NOW)
    assert not due(alert, sent, NOW + timedelta(hours=1), REPEAT)
    assert due(alert, sent, NOW + REPEAT, REPEAT)
    assert due(alert, DeliveryState(1, alert.key, "other", NOW), NOW, REPEAT)


def test_a_failure_is_retried_then_waits_for_the_repeat() -> None:
    alert = Alert(HEALTH_EVENT, AlertLevel.OUTAGE, "down", "demo", None, "", NOW, NOW)
    state = DeliveryState(1, alert.key, alert.token)
    for wait in RETRY_AFTER:
        state = after_failure(state, alert, NOW)
        assert state.retry_at == NOW + wait
        assert not due(alert, state, NOW, REPEAT)
        assert due(alert, state, NOW + wait, REPEAT)
    state = after_failure(state, alert, NOW)
    assert not due(alert, state, NOW + timedelta(hours=1), REPEAT)
    assert due(alert, state, NOW + REPEAT, REPEAT)


async def test_the_notifier_sends_once_counts_and_repeats(uow: InMemoryUnitOfWork) -> None:
    async with uow.locked() as tx:
        await tx.add_alert_target(target())
        await tx.add_alert_target(target(None))  # the platform's: not the organisation's alert
    uow.state.audit.append(down())
    senders, clock = FakeAlertSenders(), FixedClock(NOW)
    notifier = AlertNotifier(uow, clock, senders, compose, REPEAT)
    assert await notifier.run() == 1
    uow.state.audit.append(down(at=timedelta(minutes=5)))
    clock.advance(timedelta(minutes=10))
    assert await notifier.run() == 0
    clock.advance(REPEAT)
    assert await notifier.run() == 1
    assert [address for address, _ in senders.sent] == ["ops@voorbeeld.nl"] * 2
    assert senders.sent[1][1].text.endswith("|demo|1|nl")  # the counted report is named


async def test_the_level_and_the_scope_decide_who_gets_an_alert(uow: InMemoryUnitOfWork) -> None:
    async with uow.locked() as tx:
        await tx.add_alert_target(target(1, level="outage"))
        await tx.add_alert_target(target(2))
    degraded = AuditEvent(
        NOW,
        SYSTEM_ACTOR,
        HEALTH_EVENT,
        Outcome.FAILURE,
        "degraded",
        "demo",
        None,
        "platform:wxcc",
        Source.SYSTEM,
    )
    uow.state.audit.append(degraded)
    senders = FakeAlertSenders()
    assert await AlertNotifier(uow, FixedClock(NOW), senders, compose, REPEAT).run() == 0


async def test_a_failed_delivery_is_an_event_and_never_an_alert(uow: InMemoryUnitOfWork) -> None:
    async with uow.locked() as tx:
        await tx.add_alert_target(target())
    uow.state.audit.append(down())
    senders = FakeAlertSenders()
    senders.error = IntegrationError("unreachable", retryable=True)
    clock = FixedClock(NOW)
    notifier = AlertNotifier(uow, clock, senders, compose, REPEAT)
    assert await notifier.run() == 0
    (failed,) = [e for e in uow.state.audit if e.action == DELIVERY_FAILED]
    assert (failed.reason, failed.organisation) == ("unreachable", "demo")
    assert "ops@" not in failed.detail
    senders.error = None
    assert await notifier.run() == 0  # waits for the retry
    clock.advance(RETRY_AFTER[0])
    assert await notifier.run() == 1


async def test_targets_are_managed_audited_and_tested(uow: InMemoryUnitOfWork) -> None:
    senders, bots = FakeAlertSenders(), FakeBotTokens()
    service = AlertTargetService(
        uow, FixedClock(NOW), AdminService(uow, FixedClock(NOW)), senders, compose, bots
    )
    target_id = await service.add(SUPER, "demo", target())
    assert [t.id for t in await service.targets(SUPER, "demo")] == [target_id]
    assert await service.send_test(SUPER, "demo", target_id) is None
    assert senders.sent[0][1].subject == "test"
    senders.missing = {AlertChannelKind.EMAIL}
    assert await service.send_test(SUPER, "demo", target_id) == "no_sender"
    await service.set_bot(SUPER, "demo", "a" * 40)
    assert await service.has_bot(SUPER, "demo")
    with pytest.raises(DomainError):
        await service.set_bot(SUPER, "demo", "bad token with spaces")
    await service.remove(SUPER, "demo", target_id)
    details = [e.detail for e in uow.state.audit if e.outcome is audit.Outcome.SUCCESS]
    assert details[0] == f"alert_target.add,id:{target_id},kind:email"
    assert "alert_bot.set" in details
    assert not any("ops@" in d or "aaaa" in d for d in details)


async def test_only_who_may_manage_the_scope_changes_its_targets(uow: InMemoryUnitOfWork) -> None:
    service = AlertTargetService(
        uow, FixedClock(NOW), AdminService(uow, FixedClock(NOW)), FakeAlertSenders(), compose
    )
    # integrations.manage is the superuser's alone (ADR 0039), so an organisation admin is
    # refused on both scopes, as is another organisation's admin.
    for principal, organisation in ((OUTSIDER, "demo"), (ORG_ADMIN, "demo"), (ORG_ADMIN, None)):
        with pytest.raises(DomainError) as caught:
            await service.add(principal, organisation, target())
        assert caught.value.code is ErrorCode.NOT_PERMITTED
    await service.add(SUPER, None, target(None))
    assert await service.has_bot(SUPER, None) is None  # no settings key: no bot can be kept


async def test_a_scope_has_at_most_ten_targets(uow: InMemoryUnitOfWork) -> None:
    service = AlertTargetService(
        uow, FixedClock(NOW), AdminService(uow, FixedClock(NOW)), FakeAlertSenders(), compose
    )
    for _ in range(MAX_TARGETS):
        await service.add(SUPER, "demo", target())
    with pytest.raises(DomainError) as caught:
        await service.add(SUPER, "demo", target())
    assert caught.value.field == "targets"
