"""The delivery ledger (ADR 0124 points 3, 9, 10 and 12): what the gate saw, and when to say so."""

from datetime import timedelta

import pytest

from app.domain.inbound_gate import Refusal
from app.domain.webhook_ledger import (
    AddressKey,
    AlertGroup,
    Delivered,
    WebhookLedger,
    alert_group,
)
from app.domain.webhook_sources import ChainFault
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW

WINDOW = timedelta(minutes=15)
ACME = AddressKey("webex_cc", "acme")


def _ledger(
    *, threshold: int = 3, max_entries: int = 1000, dropped: list[str] | None = None
) -> tuple[WebhookLedger, FixedClock]:
    clock = FixedClock(NOW)
    told = dropped if dropped is not None else []
    return (
        WebhookLedger(clock, WINDOW, threshold, max_entries=max_entries, on_dropped=told.append),
        clock,
    )


# --- the rule: which group an outcome is counted in --------------------------------------------


def test_what_a_passer_by_cannot_cause_alone_is_the_signed_group() -> None:
    assert alert_group(Refusal.STALE) is AlertGroup.SIGNED
    assert alert_group(Delivered.UNKNOWN_WORK) is AlertGroup.SIGNED
    assert alert_group(Refusal.SIGNATURE, inside_list=True) is AlertGroup.SIGNED
    assert alert_group(Refusal.RATE, inside_list=True) is AlertGroup.SIGNED


def test_a_refusal_for_the_network_after_a_delivery_is_the_list_group() -> None:
    assert alert_group(Refusal.NETWORK, delivered_earlier=True) is AlertGroup.LIST


@pytest.mark.parametrize(
    "outcome",
    [
        Refusal.SIGNATURE,
        Refusal.TOO_LARGE,
        Refusal.CONTENT_TYPE,
        Refusal.RATE,
        Refusal.SOURCE_UNKNOWN,
        Refusal.NETWORK,
        Refusal.UNKNOWN_ADDRESS,
        Refusal.MALFORMED,
        Delivered.ACCEPTED,
        Delivered.DUPLICATE,
        Delivered.PAUSED,
    ],
)
def test_a_strangers_refusal_and_a_plain_delivery_join_no_group(
    outcome: Refusal | Delivered,
) -> None:
    assert alert_group(outcome) is None


def test_unknown_work_at_an_address_of_the_installation_is_routine_and_never_counts() -> None:
    """The platform's account is shared: another tenant's meeting or task is not a fault."""
    ledger, _ = _ledger(threshold=5)
    key = AddressKey("webex")

    results = [ledger.delivered(key, Delivered.UNKNOWN_WORK) for _ in range(50)]

    view = ledger.view(key)
    assert not any(results)
    assert view is not None
    assert (view.outcomes, view.counted) == ({"unknown_work": 50}, 0)
    assert alert_group(Delivered.UNKNOWN_WORK, organisational=False) is None


# --- counting ---------------------------------------------------------------------------------


def test_every_outcome_is_counted_per_address_and_the_times_are_kept() -> None:
    ledger, clock = _ledger()

    for outcome in Delivered:
        ledger.delivered(ACME, outcome)
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
    assert view.last_refused_at == NOW + timedelta(minutes=1)
    assert view.last_refused_reason == "malformed"
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


# --- the alerts: two groups, one each per window ------------------------------------------------


def test_the_signed_alert_comes_once_when_its_count_passes_the_threshold() -> None:
    ledger, _ = _ledger(threshold=3)

    results = [ledger.refused(ACME, Refusal.STALE) for _ in range(6)]

    alerts = [a for a in results if a is not None]
    assert results[:3] == [None, None, None]
    assert alerts == [results[3]]
    assert (alerts[0].key, alerts[0].group, alerts[0].count) == (ACME, AlertGroup.SIGNED, 4)
    assert alerts[0].reasons == ("stale",)


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
    assert after[3] is not None
    assert after[3].group is AlertGroup.LIST

    clock.advance(WINDOW)  # the next window has had no delivery yet
    assert not any(ledger.refused(ACME, Refusal.NETWORK) for _ in range(10))


@pytest.mark.parametrize("outcome", [Delivered.DUPLICATE, Delivered.PAUSED, Delivered.UNKNOWN_WORK])
def test_every_verified_outcome_shows_the_supplier_reaches_us(outcome: Delivered) -> None:
    ledger, _ = _ledger(threshold=1)
    ledger.delivered(ACME, outcome)

    ledger.refused(ACME, Refusal.NETWORK)
    alert = ledger.refused(ACME, Refusal.NETWORK)

    assert alert is not None
    assert alert.group is AlertGroup.LIST


def test_a_stranger_passing_the_list_threshold_cannot_hide_the_signed_alert() -> None:
    """The point of two groups: noise spends its own alert, not the signal's."""
    ledger, _ = _ledger(threshold=3)
    ledger.delivered(ACME, Delivered.ACCEPTED)

    noise = [ledger.refused(ACME, Refusal.NETWORK) for _ in range(10)]
    signal = [ledger.refused(ACME, Refusal.STALE) for _ in range(6)]

    assert [a.group for a in noise if a] == [AlertGroup.LIST]
    assert [a.group for a in signal if a] == [AlertGroup.SIGNED]


def test_each_group_alerts_at_most_once_per_window() -> None:
    ledger, _ = _ledger(threshold=1)
    ledger.delivered(ACME, Delivered.ACCEPTED)

    alerts = [ledger.refused(ACME, reason) for reason in (Refusal.NETWORK, Refusal.STALE) * 10]

    assert sorted(a.group for a in alerts if a) == [AlertGroup.LIST, AlertGroup.SIGNED]


def test_an_alert_that_could_not_be_written_is_raised_again_by_the_next_counted_outcome() -> None:
    ledger, _ = _ledger(threshold=1)
    ledger.refused(ACME, Refusal.STALE)
    alert = ledger.refused(ACME, Refusal.STALE)
    assert alert is not None
    assert ledger.refused(ACME, Refusal.STALE) is None  # marked

    ledger.retract(alert)

    again = ledger.refused(ACME, Refusal.STALE)
    assert again is not None
    assert (again.group, again.count) == (AlertGroup.SIGNED, 4)


def test_taking_a_mark_back_touches_neither_the_other_group_nor_a_later_window() -> None:
    ledger, clock = _ledger(threshold=1)
    ledger.delivered(ACME, Delivered.ACCEPTED)
    ledger.refused(ACME, Refusal.NETWORK)
    listed = ledger.refused(ACME, Refusal.NETWORK)
    ledger.refused(ACME, Refusal.STALE)
    signed = ledger.refused(ACME, Refusal.STALE)
    assert listed is not None
    assert signed is not None

    ledger.retract(listed)
    assert ledger.refused(ACME, Refusal.STALE) is None  # the signed mark stays

    clock.advance(WINDOW)
    ledger.refused(ACME, Refusal.STALE)
    later = ledger.refused(ACME, Refusal.STALE)
    assert later is not None
    ledger.retract(signed)  # an old window's alert: nothing to take back
    assert ledger.refused(ACME, Refusal.STALE) is None


def test_the_threshold_is_read_at_each_call() -> None:
    clock = FixedClock(NOW)
    limit = {"value": 100}
    ledger = WebhookLedger(clock, WINDOW, lambda: limit["value"])
    assert not any(ledger.refused(ACME, Refusal.STALE) for _ in range(5))

    limit["value"] = 5

    assert ledger.refused(ACME, Refusal.STALE) is not None


# --- bounded memory ---------------------------------------------------------------------------


def _key(slug: str) -> AddressKey:
    return AddressKey("webex_cc", slug)


def test_room_is_made_first_from_entries_whose_window_ended() -> None:
    ledger, clock = _ledger(threshold=1, max_entries=2)
    for _ in range(3):
        ledger.refused(_key("old"), Refusal.STALE)  # alerted
    clock.advance(WINDOW)
    ledger.refused(_key("fresh"), Refusal.MALFORMED)

    ledger.refused(_key("new"), Refusal.MALFORMED)

    assert ledger.view(_key("old")) is None  # ended: gone although it alerted
    assert ledger.view(_key("fresh")) is not None
    assert ledger.view(_key("new")) is not None


def test_then_the_oldest_entry_that_has_not_alerted_goes_and_an_alerted_one_stays() -> None:
    ledger, clock = _ledger(threshold=1, max_entries=3)
    for _ in range(3):
        ledger.refused(_key("alerted"), Refusal.STALE)  # the oldest, but alerted
    clock.advance(timedelta(seconds=1))
    ledger.refused(_key("quiet-old"), Refusal.MALFORMED)
    clock.advance(timedelta(seconds=1))
    ledger.refused(_key("quiet-new"), Refusal.MALFORMED)

    ledger.refused(_key("new"), Refusal.MALFORMED)

    assert ledger.size == 3
    assert ledger.view(_key("alerted")) is not None
    assert ledger.view(_key("quiet-old")) is None
    assert ledger.view(_key("quiet-new")) is not None
    assert ledger.view(_key("new")) is not None


def test_when_every_entry_is_live_and_alerted_the_new_address_is_not_recorded() -> None:
    dropped: list[str] = []
    ledger, _ = _ledger(threshold=1, max_entries=2, dropped=dropped)
    for slug in ("a", "b"):
        for _ in range(3):
            ledger.refused(_key(slug), Refusal.STALE)

    result = ledger.refused(_key("c"), Refusal.STALE)

    assert result is None
    assert dropped == ["webex_cc"]  # the platform's code, never the slug
    assert ledger.size == 2
    assert ledger.view(_key("c")) is None
    assert ledger.view(_key("a")) is not None
    assert ledger.view(_key("b")) is not None


def test_a_flood_over_many_addresses_stays_within_the_cap() -> None:
    ledger, _ = _ledger(max_entries=50)

    for index in range(5000):
        ledger.refused(_key(f"org{index}"), Refusal.MALFORMED)

    assert ledger.size == 50


def test_a_view_holds_codes_counts_and_times_only() -> None:
    ledger, _ = _ledger()
    ledger.delivered(ACME, Delivered.ACCEPTED)
    ledger.refused(ACME, Refusal.SOURCE_UNKNOWN, fault=ChainFault.NO_ADDRESS)

    view = ledger.view(ACME)

    assert view is not None
    assert view.outcomes == {"accepted": 1, "source_unknown": 1}
    assert view.faults == {ChainFault.NO_ADDRESS: 1}
