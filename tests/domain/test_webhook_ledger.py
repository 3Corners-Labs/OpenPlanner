"""The delivery ledger (ADR 0124 points 3, 9, 10 and 12): what the gate saw, and when to say so."""

from datetime import timedelta

from app.domain.audit import Category, Outcome, Source
from app.domain.inbound_gate import Refusal
from app.domain.webhook_ledger import (
    ALERT_ACTION,
    AddressKey,
    Delivered,
    WebhookLedger,
    counts_toward_alert,
    write_alert,
)
from app.domain.webhook_sources import ChainFault
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

WINDOW = timedelta(minutes=15)
ACME = AddressKey("webex_cc", "acme")


def _ledger(*, threshold: int = 3, max_entries: int = 1000) -> tuple[WebhookLedger, FixedClock]:
    clock = FixedClock(NOW)
    return WebhookLedger(clock, WINDOW, threshold, max_entries=max_entries), clock


# --- the rule: what the alert counts ----------------------------------------------------------


def test_only_what_a_passer_by_cannot_cause_alone_counts() -> None:
    counted = {
        "stale": counts_toward_alert(Refusal.STALE.value),
        "unknown work": counts_toward_alert(Delivered.UNKNOWN_WORK.value),
        "inside the list": counts_toward_alert(Refusal.SIGNATURE.value, inside_list=True),
        "network after a delivery": counts_toward_alert(
            Refusal.NETWORK.value, delivered_earlier=True
        ),
    }
    ignored = {
        "stranger's signature": counts_toward_alert(Refusal.SIGNATURE.value),
        "stranger's size": counts_toward_alert(Refusal.TOO_LARGE.value),
        "stranger's type": counts_toward_alert(Refusal.CONTENT_TYPE.value),
        "stranger's rate": counts_toward_alert(Refusal.RATE.value),
        "untrusted address": counts_toward_alert(Refusal.SOURCE_UNKNOWN.value),
        "network without a delivery": counts_toward_alert(Refusal.NETWORK.value),
        "no such address": counts_toward_alert(Refusal.UNKNOWN_ADDRESS.value),
        "accepted": counts_toward_alert(Delivered.ACCEPTED.value),
        "paused": counts_toward_alert(Delivered.PAUSED.value),
    }

    assert all(counted.values()), counted
    assert not any(ignored.values()), ignored


def test_unknown_work_at_an_address_of_the_installation_is_routine_and_never_counts() -> None:
    """The platform's account is shared: another tenant's meeting or task is not a fault."""
    ledger, _ = _ledger(threshold=5)
    key = AddressKey("webex")

    results = [ledger.delivered(key, Delivered.UNKNOWN_WORK) for _ in range(50)]

    view = ledger.view(key)
    assert not any(results)
    assert view is not None
    assert (view.outcomes, view.counted) == ({"unknown_work": 50}, 0)
    assert not counts_toward_alert(Delivered.UNKNOWN_WORK.value, organisational=False)


# --- counting ---------------------------------------------------------------------------------


def test_every_outcome_is_counted_per_address_and_the_times_are_kept() -> None:
    ledger, clock = _ledger()

    ledger.delivered(ACME, Delivered.ACCEPTED)
    ledger.delivered(ACME, Delivered.DUPLICATE)
    ledger.delivered(ACME, Delivered.PAUSED)
    ledger.delivered(ACME, Delivered.UNKNOWN_WORK)
    clock.advance(timedelta(minutes=1))
    ledger.refused(ACME, Refusal.SIGNATURE)
    ledger.refused(ACME, Refusal.SIGNATURE)
    ledger.refused(ACME, Refusal.MALFORMED)

    view = ledger.view(ACME)
    assert view is not None
    assert view.outcomes == {
        "accepted": 1,
        "duplicate": 1,
        "paused": 1,
        "unknown_work": 1,
        "invalid_signature": 2,
        "malformed": 1,
    }
    assert view.last_accepted == NOW
    assert (view.last_refused_at, view.last_refused_reason) == (
        NOW + timedelta(minutes=1),
        "malformed",
    )
    assert view.paused_dropped == 1


def test_an_address_the_ledger_never_saw_has_no_view() -> None:
    ledger, _ = _ledger()

    assert ledger.view(ACME) is None


def test_the_faults_of_a_call_are_counted_by_kind_and_inside_refusals_apart() -> None:
    ledger, _ = _ledger()

    ledger.refused(ACME, Refusal.SOURCE_UNKNOWN, fault=ChainFault.CALLER_IS_PROXY)
    ledger.refused(ACME, Refusal.SOURCE_UNKNOWN, fault=ChainFault.CALLER_IS_PROXY)
    ledger.refused(ACME, Refusal.SOURCE_UNKNOWN, fault=ChainFault.NO_ADDRESS)
    ledger.refused(ACME, Refusal.STALE, inside_list=True)

    view = ledger.view(ACME)
    assert view is not None
    assert view.faults == {ChainFault.CALLER_IS_PROXY: 2, ChainFault.NO_ADDRESS: 1}
    assert view.inside_list == 1


def test_counts_start_again_with_the_next_window_but_the_last_times_stay() -> None:
    ledger, clock = _ledger()
    ledger.delivered(ACME, Delivered.ACCEPTED)
    ledger.refused(ACME, Refusal.MALFORMED)

    clock.advance(WINDOW)
    quiet = ledger.view(ACME)
    ledger.refused(ACME, Refusal.CONTENT_TYPE)
    fresh = ledger.view(ACME)

    assert quiet is not None
    assert quiet.outcomes == {}
    assert quiet.last_accepted == NOW
    assert fresh is not None
    assert fresh.outcomes == {"content_type": 1}
    assert fresh.last_accepted == NOW
    assert fresh.window_started == NOW + WINDOW


def test_an_unknown_address_counts_under_its_platform_and_never_under_a_slug() -> None:
    ledger, _ = _ledger()

    for _ in range(3):
        ledger.refused(AddressKey("webex_cc", unknown=True), Refusal.UNKNOWN_ADDRESS)

    view = ledger.view(AddressKey("webex_cc", unknown=True))
    assert view is not None
    assert view.outcomes == {"unknown_address": 3}
    assert ledger.view(AddressKey("webex_cc")) is None
    assert ledger.size == 1


# --- the alert --------------------------------------------------------------------------------


def test_the_alert_comes_once_when_the_counted_refusals_pass_the_threshold() -> None:
    ledger, _ = _ledger(threshold=3)

    results = [ledger.refused(ACME, Refusal.STALE) for _ in range(6)]

    alerts = [a for a in results if a is not None]
    assert results[:3] == [None, None, None]
    assert len(alerts) == 1
    assert results[3] is alerts[0]
    assert (alerts[0].key, alerts[0].count, alerts[0].reasons) == (ACME, 4, ("stale",))


def test_the_alert_names_the_reasons_it_counted_and_nothing_else() -> None:
    ledger, _ = _ledger(threshold=3)

    ledger.refused(ACME, Refusal.STALE)
    ledger.delivered(ACME, Delivered.UNKNOWN_WORK)
    ledger.refused(ACME, Refusal.MALFORMED, inside_list=True)
    ledger.refused(ACME, Refusal.SIGNATURE)  # a stranger's: not counted
    alert = ledger.refused(ACME, Refusal.STALE)

    assert alert is not None
    assert alert.count == 4
    assert alert.reasons == ("malformed", "stale", "unknown_work")


def test_a_second_window_may_raise_a_second_alert() -> None:
    ledger, clock = _ledger(threshold=2)
    first = [ledger.refused(ACME, Refusal.STALE) for _ in range(5)]

    clock.advance(WINDOW)
    second = [ledger.refused(ACME, Refusal.STALE) for _ in range(5)]

    assert sum(a is not None for a in first) == 1
    assert sum(a is not None for a in second) == 1


def test_strangers_alone_raise_nothing_however_many() -> None:
    ledger, _ = _ledger(threshold=3)

    results = [
        ledger.refused(ACME, reason)
        for reason in (Refusal.SIGNATURE, Refusal.NETWORK, Refusal.RATE, Refusal.TOO_LARGE)
        for _ in range(50)
    ]

    assert not any(results)


def test_a_refusal_for_the_network_counts_only_after_a_delivery_in_the_same_window() -> None:
    ledger, clock = _ledger(threshold=3)
    before = [ledger.refused(ACME, Refusal.NETWORK) for _ in range(10)]
    ledger.delivered(ACME, Delivered.ACCEPTED)
    after = [ledger.refused(ACME, Refusal.NETWORK) for _ in range(4)]

    assert not any(before)
    assert [a is not None for a in after] == [False, False, False, True]

    clock.advance(WINDOW)  # the next window has had no delivery yet
    assert not any(ledger.refused(ACME, Refusal.NETWORK) for _ in range(10))


def test_every_verified_outcome_shows_the_supplier_reaches_us() -> None:
    for outcome in (Delivered.DUPLICATE, Delivered.PAUSED, Delivered.UNKNOWN_WORK):
        ledger, _ = _ledger(threshold=99)
        ledger.delivered(ACME, outcome)
        ledger.refused(ACME, Refusal.NETWORK)

        view = ledger.view(ACME)
        assert view is not None
        assert view.counted == (2 if outcome is Delivered.UNKNOWN_WORK else 1)


def test_the_threshold_is_read_at_each_call() -> None:
    clock = FixedClock(NOW)
    limit = {"value": 100}
    ledger = WebhookLedger(clock, WINDOW, lambda: limit["value"])
    assert not any(ledger.refused(ACME, Refusal.STALE) for _ in range(5))

    limit["value"] = 5

    assert ledger.refused(ACME, Refusal.STALE) is not None


# --- bounded memory ---------------------------------------------------------------------------


def test_the_ledger_never_holds_more_entries_than_its_cap_and_drops_the_oldest_first() -> None:
    ledger, clock = _ledger(max_entries=3)
    for slug in ("a", "b", "c"):
        ledger.refused(AddressKey("webex_cc", slug), Refusal.MALFORMED)
        clock.advance(timedelta(seconds=1))

    ledger.refused(AddressKey("webex_cc", "d"), Refusal.MALFORMED)

    assert ledger.size == 3
    assert ledger.view(AddressKey("webex_cc", "a")) is None
    assert ledger.view(AddressKey("webex_cc", "d")) is not None


def test_a_flood_over_many_addresses_stays_within_the_cap() -> None:
    ledger, _ = _ledger(max_entries=50)

    for index in range(5000):
        ledger.refused(AddressKey("webex_cc", f"org{index}"), Refusal.MALFORMED)

    assert ledger.size == 50


def test_a_view_holds_codes_counts_and_times_only() -> None:
    ledger, _ = _ledger()
    ledger.delivered(ACME, Delivered.ACCEPTED)
    ledger.refused(ACME, Refusal.SOURCE_UNKNOWN, fault=ChainFault.NO_ADDRESS)

    view = ledger.view(ACME)

    assert view is not None
    texts = {*view.outcomes, *(fault.value for fault in view.faults)}
    assert texts <= {"accepted", "source_unknown", "no_address"}
    assert all(isinstance(n, int) for n in (*view.outcomes.values(), *view.faults.values()))


# --- the event --------------------------------------------------------------------------------


async def test_the_alert_is_one_system_event_for_the_organisation_with_codes_only() -> None:
    ledger, _ = _ledger(threshold=1)
    ledger.refused(ACME, Refusal.STALE)
    alert = ledger.refused(ACME, Refusal.MALFORMED, inside_list=True)
    assert alert is not None
    uow = InMemoryUnitOfWork(State())

    await write_alert(uow, alert, NOW)

    (event,) = uow.state.audit
    assert (event.action, event.outcome, event.source) == (
        ALERT_ACTION,
        Outcome.FAILURE,
        Source.SYSTEM,
    )
    assert (event.organisation, event.group, event.category) == ("acme", None, Category.SYSTEM)
    assert event.detail == "platform:webex_cc,count:2,reasons:malformed+stale"
    assert event.reason == "too_many_refusals"


async def test_an_alert_for_an_installation_address_names_no_organisation() -> None:
    ledger, _ = _ledger(threshold=1)
    key = AddressKey("webex")
    ledger.refused(key, Refusal.STALE)
    alert = ledger.refused(key, Refusal.STALE)
    assert alert is not None
    uow = InMemoryUnitOfWork(State())

    await write_alert(uow, alert, NOW)

    (event,) = uow.state.audit
    assert event.organisation is None
    assert event.detail.startswith("platform:webex,")
