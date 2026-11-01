"""Booking form screen (ADR 0055): add, edit, move and retire custom fields; answers shown."""

from dataclasses import replace
from datetime import UTC, datetime

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.services import BookingService
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site
from tests.support.domain import ADA, TEST_TOKEN_KEY

PAGE = "/admin/groups/demo/settings/form"
FRIDAY_0900 = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
TOPIC = {
    "key": "topic",
    "type": "choice",
    "label_nl": "Onderwerp",
    "label_en": "Subject",
    "classification": "not_pii",
    "confirm_not_pii": "yes",
    "options": "rent; Huur; Rent\ncare; Zorg; Care",
}
CUSTOMER = {
    "key": "customer_no",
    "type": "short_text",
    "label_nl": "Klantnummer",
    "label_en": "Customer number",
    "classification": "pii",
    "required": "yes",
}


async def _details(site: Site) -> list[str]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=50)
    return [e.event.detail for e in reversed(events) if e.event.action == "group.edit"]


async def _post(site: Site, url: str, data: dict[str, str]) -> httpx.Response:
    return await site.client.post(url, data=data, headers=SAME_ORIGIN)


async def test_the_settings_link_to_the_booking_form(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    settings = (await site.client.get("/admin/groups/demo/settings")).text
    page = (await site.client.get(PAGE)).text

    assert f'href="{PAGE}"' in settings
    assert "Waar gaat uw vraag over?" in page  # the demo's example fields
    assert "<code>tolk</code>" in page
    assert 'aria-current="page"' in page  # the Settings tab stays selected


async def test_the_settings_card_summarises_the_booking_form(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    card = (await site.client.get("/admin/groups/demo/settings")).text
    await _post(site, PAGE, CUSTOMER)
    after = (await site.client.get("/admin/groups/demo/settings")).text

    assert "Waar gaat uw vraag over?" in card  # the questions are named on the card
    assert "van maximaal 5" in card
    assert "Klantnummer" in after
    assert 'class="button button--secondary"' in after
    assert f'href="{PAGE}"' in after


async def test_fields_are_added_listed_and_audited_by_key(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    added = await _post(site, PAGE, CUSTOMER)
    await _post(site, PAGE, TOPIC)

    assert added.headers["location"] == f"{PAGE}?done=created"
    page = (await site.client.get(PAGE)).text
    assert page.index("Klantnummer") < page.index("Onderwerp")
    assert "<code>customer_no</code>" in page
    assert "Geen persoonsgegeven" in page
    details = await _details(site)
    assert details[-2:] == [
        "field.add,key:customer_no,type:short_text",
        "field.add,key:topic,type:choice",
    ]


async def test_refusals_are_linked_to_their_inputs(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    no_confirm = await _post(site, PAGE, {**TOPIC, "confirm_not_pii": ""})
    forbidden = await _post(site, PAGE, {**CUSTOMER, "key": "bsn"})
    bad_options = await _post(site, PAGE, {**TOPIC, "options": "rent; Huur"})
    no_english = await _post(site, PAGE, {**CUSTOMER, "label_en": ""})

    assert {r.status_code for r in (no_confirm, forbidden, bad_options, no_english)} == {422}
    assert '<a href="#confirm_not_pii">Bevestig' in no_confirm.text
    assert '<a href="#key">Dit veld vraagt om gegevens die we niet mogen vragen' in forbidden.text
    assert '<a href="#options">Geef 1 tot 30 keuzes' in bad_options.text
    assert '<a href="#label_nl">Vul de vraag in het Nederlands en het Engels in' in no_english.text
    assert 'value="Klantnummer"' in no_english.text  # what was typed stays


async def test_a_field_is_edited_moved_and_retired(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _post(site, PAGE, CUSTOMER)
    await _post(site, PAGE, TOPIC)

    edit = (await site.client.get(f"{PAGE}/topic")).text
    assert "rent; Huur; Rent\ncare; Zorg; Care</textarea>" in edit
    assert 'name="type"' not in edit  # key and type are fixed
    saved = await _post(site, f"{PAGE}/topic", {**TOPIC, "label_nl": "Waarover?"})
    assert saved.headers["location"] == f"{PAGE}?done=saved"
    unchanged = await _post(site, f"{PAGE}/topic", {**TOPIC, "label_nl": "Waarover?"})
    assert unchanged.headers["location"] == f"{PAGE}?done=unchanged"
    refused = await _post(site, f"{PAGE}/topic", {**TOPIC, "label_en": ""})
    assert refused.status_code == 422

    await _post(site, f"{PAGE}/topic/move", {"direction": "up"})
    page = (await site.client.get(PAGE)).text
    assert page.index("Waarover?") < page.index("Klantnummer")

    retired = await _post(site, f"{PAGE}/customer_no/retire", {})
    assert retired.headers["location"] == f"{PAGE}?done=field_retired#fields"
    page = (await site.client.get(PAGE)).text
    assert "Uitgezet (alleen nog bij eerdere afspraken): Klantnummer" in page
    again = await _post(site, PAGE, CUSTOMER)
    assert '<a href="#key">Kies een sleutel' in again.text


async def test_a_group_admin_cannot_open_the_form(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    assert (await site.client.get(PAGE)).status_code == 403


async def test_the_booking_page_shows_the_answers_with_their_labels(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _post(site, PAGE, CUSTOMER)
    await _post(site, PAGE, TOPIC)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    receipt = await BookingService(
        SqlUnitOfWork(app.state.engine), site.clock, TEST_TOKEN_KEY
    ).book(
        group_slug="demo",
        start=FRIDAY_0900,
        contact=replace(ADA, answers={"customer_no": "A-12", "topic": "care"}),
        locale="nl",
    )

    text = (await site.client.get(f"/admin/groups/demo/bookings/{receipt.booking.id}")).text

    assert "<dt>Klantnummer</dt><dd>A-12</dd>" in text
    assert "<dt>Onderwerp</dt><dd>Zorg</dd>" in text
