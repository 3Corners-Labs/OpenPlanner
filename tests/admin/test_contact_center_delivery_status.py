"""What the gate saw at an organisation's address: the card, the alert and the pause on the status
screens (ADR 0124 points 3, 9, 10 and 12, step 3b)."""

from datetime import timedelta

import httpx

from app.domain.audit import Category
from app.domain.inbound_gate import Refusal
from app.domain.integration import EventOutcome
from app.domain.webhook_ledger import AddressKey, WebhookLedger
from app.web.webhook_ledger import ALERT_ACTION
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import site_app
from tests.support.contact_center_site import (
    FORM,
    PAGE,
    STRANGER,
    SUPPLIER,
    SUPPLIER_RANGE,
    _call_from,
    _connect,
    _pause,
    _save_networks,
    _set_settings,
)

WINDOW = timedelta(minutes=15)
FORGED = "x" * 43
DEMO = AddressKey("wxcc", "demo")
SIGNED_BAD_SIGNATURE = "platform:wxcc,group:signed,count:6,reasons:invalid_signature"


class _Handling:
    """Stands in for the integration service: every verified event comes to ``outcome``."""

    def __init__(self, outcome: EventOutcome) -> None:
        self.outcome = outcome

    async def record_task_event(self, *args: object, **kwargs: object) -> EventOutcome:
        return self.outcome


def _handling(site: Site, outcome: EventOutcome) -> None:
    site_app(site).state.integration_service = _Handling(outcome)


def _ledger(site: Site) -> WebhookLedger:
    ledger: WebhookLedger = site_app(site).state.webhook_ledger
    return ledger


async def _alerts(site: Site) -> list[tuple[str | None, str, str]]:
    """Return ``(organisation, reason, detail)`` of every refusal alert, newest first."""
    log = await site_app(site).state.admin_service.audit_log(SUPER)
    return [
        (e.event.organisation, e.event.reason, e.event.detail)
        for e in log
        if e.event.action == ALERT_ACTION
    ]


async def _send(site: Site, caller: str, times: int, secret: str | None = None) -> list[int]:
    return [(await _call_from(site, caller, secret=secret)).status_code for _ in range(times)]


async def _connected_with_alert_at(site: Site, count: int = 5, networks: str = "") -> None:
    await _connect(site)
    if networks:
        await _save_networks(site, networks)
    _set_settings(site, webhook_refusal_alert_count=count)


# --- the card ---------------------------------------------------------------------------------


async def test_the_card_says_what_was_last_accepted_and_refused_and_counts_per_reason(
    site: Site,
) -> None:
    await _connect(site)
    _handling(site, EventOutcome.RECORDED)
    await _send(site, SUPPLIER, 2)
    site.clock.advance(timedelta(minutes=1))
    await _send(site, SUPPLIER, 3, secret=FORGED)

    card = (await site.client.get(PAGE)).text

    assert "Geziene bezorgingen" in card
    assert "Laatst geaccepteerd" in card
    assert "Laatst geweigerd" in card
    assert "Geweigerd: handtekening klopt niet" in card
    assert "Geaccepteerd en verwerkt" in card
    assert "Deze aantallen zijn van het draaiende webproces" in card
    assert "Gebeurtenissen weggegooid tijdens de pauze" in card
    assert _row(card, "Geaccepteerd en verwerkt") == "2"
    assert _row(card, "Geweigerd: handtekening klopt niet") == "3"


def _row(page: str, label: str) -> str:
    """Return the count cell of the table row whose heading cell is ``label``."""
    cell = page.split(f">{label}</th><td>", 1)[1]
    return cell.split("</td>", 1)[0]


async def test_the_card_shows_words_never_codes_and_needs_no_script(site: Site) -> None:
    await _connect(site)
    await _save_networks(site, SUPPLIER_RANGE)
    _handling(site, EventOutcome.DUPLICATE)
    await _send(site, STRANGER, 1)  # network
    await _send(site, SUPPLIER, 1)  # a duplicate
    await _send(site, SUPPLIER, 1, secret=FORGED)  # inside the list

    page = (await site.client.get(PAGE)).text
    card = page.split('id="contact-center"', 1)[1].split("</section>", 1)[0]

    for code in ("invalid_signature", "unknown_work", "source_unknown", "duplicate"):
        assert code not in card, code
    assert "<script" not in card
    assert "Geweigerd: aanroeper buiten de toegestane netwerken" in card
    assert "1 van de geweigerde aanroepen kwam van binnen de eigen netwerklijst" in card


async def test_a_fresh_card_says_nothing_has_been_seen_yet(site: Site) -> None:
    await _connect(site)

    card = (await site.client.get(PAGE)).text

    assert card.count("Geen gezien sinds dit proces startte") == 2
    assert "Geen aanroepen in dit venster." in card


async def test_the_counts_start_again_with_the_next_window_but_the_last_times_stay(
    site: Site,
) -> None:
    await _connect(site)
    _handling(site, EventOutcome.RECORDED)
    await _send(site, SUPPLIER, 1)
    await _send(site, SUPPLIER, 1, secret=FORGED)

    site.clock.advance(WINDOW)
    card = (await site.client.get(PAGE)).text

    assert "Geen aanroepen in dit venster." in card
    assert card.count("Geen gezien sinds dit proces startte") == 0  # the last times stay


async def test_the_card_shows_the_faults_of_single_calls_by_kind(site: Site) -> None:
    await _connect(site)
    await _save_networks(site, SUPPLIER_RANGE)
    _set_settings(site, forwarded_allow_ips="*")  # every address could be spoofed

    assert await _send(site, SUPPLIER, 2) == [403, 403]

    card = (await site.client.get(PAGE)).text
    assert "Het vertrouwen in de proxy is ruimer dan losse hosts" in card
    assert _row(card, "Het vertrouwen in de proxy is ruimer dan losse hosts") == "2"


# --- the alert --------------------------------------------------------------------------------


async def test_refusals_from_inside_the_list_raise_one_alert_past_the_threshold(
    site: Site,
) -> None:
    await _connected_with_alert_at(site, networks=SUPPLIER_RANGE)

    await _send(site, SUPPLIER, 5, secret=FORGED)
    assert await _alerts(site) == []  # at the threshold, not past it
    await _send(site, SUPPLIER, 20, secret=FORGED)

    assert await _alerts(site) == [
        (
            "demo",
            "too_many_refusals",
            "platform:wxcc,group:signed,count:6,reasons:invalid_signature",
        )
    ]


async def test_the_alert_is_a_system_event_for_the_organisation_with_codes_only(
    site: Site,
) -> None:
    await _connected_with_alert_at(site, networks=SUPPLIER_RANGE)

    await _send(site, SUPPLIER, 6, secret=FORGED)

    log = await site_app(site).state.admin_service.audit_log(SUPER)
    (alert,) = [e.event for e in log if e.event.action == ALERT_ACTION]
    assert (alert.category, alert.organisation, alert.group) == (Category.SYSTEM, "demo", None)
    for forbidden in (SUPPLIER, STRANGER, FORGED, "client:"):
        assert forbidden not in alert.detail + alert.reason + alert.actor


async def test_strangers_who_know_the_slug_cannot_raise_the_alert(site: Site) -> None:
    """Abuse case: anyone can send failing calls to an address; that alone raises nothing."""
    await _connected_with_alert_at(site, count=5, networks=SUPPLIER_RANGE)
    await _send(site, STRANGER, 40)  # outside the list: refused for the network
    await _send(site, STRANGER, 40, secret=FORGED)

    assert await _alerts(site) == []
    shown = _ledger(site).view(DEMO)
    assert shown is not None
    assert shown.outcomes == {"network": 80}
    assert shown.counted == 0


async def test_strangers_without_any_list_cannot_raise_the_alert_either(site: Site) -> None:
    await _connected_with_alert_at(site, count=5)

    await _send(site, STRANGER, 40, secret=FORGED)

    assert await _alerts(site) == []


async def test_a_refusal_for_the_network_counts_once_a_delivery_was_accepted_in_the_window(
    site: Site,
) -> None:
    """ADR 0124 point 3: the list may have gone stale (the supplier's ranges changed)."""
    await _connected_with_alert_at(site, networks=SUPPLIER_RANGE)
    _handling(site, EventOutcome.RECORDED)
    await _send(site, SUPPLIER, 1)

    await _send(site, STRANGER, 6)

    assert await _alerts(site) == [
        ("demo", "too_many_refusals", "platform:wxcc,group:list,count:6,reasons:network")
    ]


async def test_events_about_work_the_connection_did_not_create_raise_the_alert(
    site: Site,
) -> None:
    """ADR 0124 point 12: usually a subscription that points at the wrong address."""
    await _connected_with_alert_at(site)
    _handling(site, EventOutcome.UNKNOWN)

    assert await _send(site, SUPPLIER, 6) == [204] * 6  # answered as always

    assert await _alerts(site) == [
        ("demo", "too_many_refusals", "platform:wxcc,group:signed,count:6,reasons:unknown_work")
    ]
    shown = _ledger(site).view(DEMO)
    assert shown is not None
    assert shown.outcomes == {"unknown_work": 6}


async def test_the_alert_is_written_once_per_window(site: Site) -> None:
    await _connected_with_alert_at(site, networks=SUPPLIER_RANGE)

    await _send(site, SUPPLIER, 12, secret=FORGED)
    once = await _alerts(site)
    site.clock.advance(WINDOW)
    await _send(site, SUPPLIER, 6, secret=FORGED)

    assert len(once) == 1
    assert len(await _alerts(site)) == 2  # the next window may raise it again


async def test_the_threshold_is_the_platform_setting_read_live(site: Site) -> None:
    await _connected_with_alert_at(site, count=50, networks=SUPPLIER_RANGE)
    await _send(site, SUPPLIER, 10, secret=FORGED)
    assert await _alerts(site) == []

    _set_settings(site, webhook_refusal_alert_count=5)
    await _send(site, SUPPLIER, 1, secret=FORGED)

    assert len(await _alerts(site)) == 1


async def test_a_failure_to_write_the_alert_changes_no_answer(site: Site) -> None:
    await _connected_with_alert_at(site, networks=SUPPLIER_RANGE)

    async def broken(alert: object) -> None:
        raise RuntimeError("the database is gone")

    site_app(site).state.webhook_alert_writer = broken

    statuses = await _send(site, SUPPLIER, 8, secret=FORGED)
    signed = await _send(site, SUPPLIER, 1)

    assert statuses == [401] * 8
    assert signed == [204]
    assert await _alerts(site) == []


# --- bounded memory, no writes, no personal data ----------------------------------------------


async def test_a_flood_costs_bounded_memory_and_no_database_write_of_its_own(site: Site) -> None:
    await _connect(site)
    before = len(await site_app(site).state.admin_service.audit_log(SUPER))

    await _send(site, STRANGER, 60, secret=FORGED)
    for slug in range(40):  # addresses that do not exist: one entry for all of them
        await site.client.post(f"/webhooks/contact-center/webex_cc/nobody{slug}", content=b"{}")

    log = await site_app(site).state.admin_service.audit_log(SUPER)
    new = [e.event.action for e in log[: len(log) - before]]
    assert set(new) <= {"webhook.rejected"}  # the security log's one event per kind and window
    assert len(new) <= 3
    assert _ledger(site).size == 2  # the organisation's address and "no such address"
    unknown = _ledger(site).view(AddressKey("wxcc", unknown=True))
    assert unknown is not None
    assert unknown.outcomes == {"unknown_address": 40}


async def test_the_ledger_holds_no_address_pseudonym_or_signature(site: Site) -> None:
    await _connect(site)
    await _send(site, STRANGER, 3, secret=FORGED)
    await _send(site, SUPPLIER, 1)

    shown = _ledger(site).view(DEMO)
    inside = repr(_ledger(site).__dict__)

    assert shown is not None
    for forbidden in (STRANGER, SUPPLIER, FORGED, "client", "testserver"):
        assert forbidden not in inside + repr(shown), forbidden


# --- the pause on the status screens ----------------------------------------------------------


async def test_the_events_dropped_while_paused_are_counted_on_the_card(site: Site) -> None:
    await _connect(site)
    _handling(site, EventOutcome.RECORDED)
    await _pause(site, "off")

    await _send(site, SUPPLIER, 3)
    await _send(site, SUPPLIER, 2, secret=FORGED)  # refused as ever, not "dropped"

    card = (await site.client.get(PAGE)).text
    assert _row(card, "Gecontroleerd, weggegooid tijdens de pauze") == "3"
    assert _row(card, "Geweigerd: handtekening klopt niet") == "2"
    assert "Geaccepteerd en verwerkt</th>" not in card  # nothing was accepted


async def _page(site: Site, path: str) -> str:
    return (await site.client.get(path)).text


async def test_a_paused_address_reads_as_paused_on_koppelingen_and_the_overview(
    site: Site,
) -> None:
    await _connect(site)
    _handling(site, EventOutcome.RECORDED)
    await _send(site, SUPPLIER, 1)
    before = await _page(site, "/admin/integrations")

    await _pause(site, "off")
    await _send(site, SUPPLIER, 4)
    integrations = await _page(site, "/admin/integrations")
    overview = await _page(site, "/admin/")

    assert "Gepauzeerde adressen" not in before
    assert "Gepauzeerde adressen" in integrations
    assert (
        '<th scope="row"><a href="/admin/organisations/demo/channels#contact-center">demo</a>'
        in (integrations)
    )
    assert "<td>4</td>" in integrations  # the events dropped while it lasts
    assert "1 adres(sen) gepauzeerd" in overview


async def test_a_resumed_address_is_no_longer_shown_as_paused(site: Site) -> None:
    await _connect(site)
    await _pause(site, "off")
    await _pause(site, "on")

    assert "Gepauzeerde adressen" not in await _page(site, "/admin/integrations")
    assert "gepauzeerd" not in await _page(site, "/admin/")


async def test_the_card_stays_behind_the_connection_right(site: Site) -> None:
    """Only someone who may manage the connection sees its card: the page's own permission."""
    await _connect(site)
    await _send(site, STRANGER, 1, secret=FORGED)
    await site.login_as(ORG_ADMIN)

    page: httpx.Response = await site.client.get(PAGE)

    assert page.status_code == 403
    assert "Geziene bezorgingen" not in page.text


async def test_an_administrator_of_one_organisation_sees_nothing_of_another(site: Site) -> None:
    """Not the card, not the counts, not the paused state on the status screens."""
    await _connect(site)
    other = "/admin/organisations/andere/channels"
    saved = await site.client.post(f"{other}/contact-center", data=FORM, headers=SAME_ORIGIN)
    assert saved.status_code == 303
    await _pause(site, "off", org="andere")
    _ledger(site).refused(AddressKey("wxcc", "andere"), Refusal.STALE)
    await site.login_as(ORG_ADMIN)  # administrator of ``demo`` only

    card = await site.client.get(other)
    integrations = await _page(site, "/admin/integrations")
    overview = await _page(site, "/admin/")

    assert card.status_code == 403
    assert "Geziene bezorgingen" not in card.text
    assert "Gepauzeerde adressen" not in integrations
    assert "andere" not in integrations
    assert "gepauzeerd" not in overview


# --- the two alert groups end to end ----------------------------------------------------------


async def test_a_stranger_spending_the_list_alert_cannot_hide_the_signed_one(site: Site) -> None:
    await _connected_with_alert_at(site, networks=SUPPLIER_RANGE)
    _handling(site, EventOutcome.RECORDED)
    await _send(site, SUPPLIER, 1)

    await _send(site, STRANGER, 6)  # the list group: a stranger can cause this
    await _send(site, SUPPLIER, 6, secret=FORGED)  # the signed group: from inside the list

    assert sorted(detail.split(",")[1] for _, _, detail in await _alerts(site)) == [
        "group:list",
        "group:signed",
    ]


async def test_an_alert_that_could_not_be_written_is_raised_by_the_next_counted_call(
    site: Site,
) -> None:
    await _connected_with_alert_at(site, networks=SUPPLIER_RANGE)
    state = site_app(site).state
    working = state.webhook_alert_writer

    async def broken(alert: object) -> None:
        raise RuntimeError("the database is gone")

    state.webhook_alert_writer = broken
    assert await _send(site, SUPPLIER, 6, secret=FORGED) == [401] * 6
    assert await _alerts(site) == []

    state.webhook_alert_writer = working
    await _send(site, SUPPLIER, 1, secret=FORGED)

    assert len(await _alerts(site)) == 1
