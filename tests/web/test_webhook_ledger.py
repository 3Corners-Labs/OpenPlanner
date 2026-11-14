"""The web side of the delivery ledger: the alert's event (ADR 0124 point 10)."""

from datetime import timedelta

from app.domain.audit import Category, Outcome, Source
from app.domain.inbound_gate import Refusal
from app.domain.webhook_ledger import AddressKey, Alert, Delivered, WebhookLedger
from app.web.webhook_ledger import ALERT_ACTION, write_alert
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW


def _alert(key: AddressKey, reasons: list[Refusal]) -> Alert:
    """Return the alert the ledger raises when ``reasons`` are refused after a delivery."""
    ledger = WebhookLedger(FixedClock(NOW), timedelta(minutes=15), 1)
    ledger.delivered(key, Delivered.ACCEPTED)
    for reason in reasons:
        alert = ledger.refused(key, reason, inside_list=reason is Refusal.MALFORMED)
        if alert is not None:
            return alert
    raise AssertionError("no alert")


async def test_the_alert_is_one_system_event_for_the_organisation_with_codes_only() -> None:
    alert = _alert(AddressKey("webex_cc", "acme"), [Refusal.STALE, Refusal.MALFORMED])
    uow = InMemoryUnitOfWork(State())

    await write_alert(uow, alert, NOW)

    (event,) = uow.state.audit
    assert (event.action, event.outcome, event.source) == (
        ALERT_ACTION,
        Outcome.FAILURE,
        Source.SYSTEM,
    )
    assert (event.organisation, event.group, event.category) == ("acme", None, Category.SYSTEM)
    assert event.reason == "too_many_refusals"
    assert event.detail == "platform:webex_cc,group:signed,count:2,reasons:malformed+stale"


async def test_the_list_alert_names_its_group() -> None:
    alert = _alert(AddressKey("webex_cc", "acme"), [Refusal.NETWORK, Refusal.NETWORK])
    uow = InMemoryUnitOfWork(State())

    await write_alert(uow, alert, NOW)

    (event,) = uow.state.audit
    assert event.detail == "platform:webex_cc,group:list,count:2,reasons:network"


async def test_an_alert_for_an_installation_address_names_no_organisation() -> None:
    alert = _alert(AddressKey("webex"), [Refusal.STALE, Refusal.STALE])
    uow = InMemoryUnitOfWork(State())

    await write_alert(uow, alert, NOW)

    (event,) = uow.state.audit
    assert event.organisation is None
    assert event.detail.startswith("platform:webex,group:signed,")
