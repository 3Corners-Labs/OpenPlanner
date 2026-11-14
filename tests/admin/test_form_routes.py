"""Booking form screen (ADR 0055): add, edit, move and retire custom fields; answers shown."""

from dataclasses import replace

from app.domain.services import BookingService
from tests.support.admin_site import GROUP_ADMIN, ORG_ADMIN, Site, audit_log, post_form, site_uow
from tests.support.domain import ADA, FRIDAY_0900, TEST_TOKEN_KEY

PAGE = "/admin/groups/demo/settings/form"
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
    return [e.detail for e in await audit_log(site) if e.action == "group.edit"]


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
    await post_form(site, PAGE, CUSTOMER)
    after = (await site.client.get("/admin/groups/demo/settings")).text

    assert "Waar gaat uw vraag over?" in card  # the questions are named on the card
    assert "van maximaal 5" in card
    assert "Klantnummer" in after
    assert 'class="button button--secondary"' in after
    assert f'href="{PAGE}"' in after


async def test_the_group_settings_list_their_topics_by_right(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    org_admin = (await site.client.get("/admin/groups/demo/settings")).text
    await site.login_as(GROUP_ADMIN)
    group_admin = (await site.client.get("/admin/groups/demo/settings")).text

    for anchor in ("general", "integrations", "idp-key", "booking-form", "check"):
        assert f'href="#{anchor}"' in org_admin  # "On this page" names every topic
        assert f'id="{anchor}"' in org_admin  # and each link has its target
    assert 'href="#archive"' in org_admin
    assert 'href="#archive"' not in group_admin  # archiving needs group.delete


async def test_fields_are_added_listed_and_audited_by_key(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    added = await post_form(site, PAGE, CUSTOMER)
    await post_form(site, PAGE, TOPIC)

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


async def test_the_key_is_made_from_the_question_when_left_empty(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    without_key = {k: v for k, v in CUSTOMER.items() if k != "key"}

    first = await post_form(site, PAGE, without_key)
    second = await post_form(site, PAGE, without_key)

    assert first.headers["location"] == f"{PAGE}?done=created"
    assert second.headers["location"] == f"{PAGE}?done=created"
    assert (await _details(site))[-2:] == [
        "field.add,key:klantnummer,type:short_text",
        "field.add,key:klantnummer_2,type:short_text",
    ]


async def test_a_forbidden_question_without_a_key_is_refused_at_the_question(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await post_form(
        site, PAGE, {**CUSTOMER, "key": "", "label_nl": "Social security number", "label_en": "SSN"}
    )

    assert refused.status_code == 422
    assert '<a href="#label_nl">' in refused.text  # the administrator never typed a key


async def test_the_administrator_first_chooses_what_kind_of_question(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(PAGE)).text

    for kind in ("short_text", "long_text", "email", "phone", "number", "date", "postcode_nl"):
        assert f'href="{PAGE}?type={kind}#add-field"' in page
    assert f'href="{PAGE}?type=choice#add-field"' in page
    assert 'id="label_nl"' not in page  # the form comes after the choice


async def test_the_form_of_a_kind_asks_the_question_first_and_the_key_last(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(f"{PAGE}?type=short_text")).text

    order = [page.index(f'id="{name}"') for name in ("label_nl", "classification", "key")]
    assert order == sorted(order)
    assert '<input type="hidden" name="type" value="short_text">' in page
    assert 'id="options"' not in page  # only a choice has options
    assert 'id="minimum"' not in page


async def test_the_form_proposes_the_classification_of_its_kind(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get(f"{PAGE}?type=short_text")).text
    choice = (await site.client.get(f"{PAGE}?type=choice")).text
    email = (await site.client.get(f"{PAGE}?type=email")).text
    number = (await site.client.get(f"{PAGE}?type=number")).text

    assert '<option value="pii" selected>' in text
    assert '<option value="not_pii" selected>' in choice  # still needs the confirmation
    assert 'id="options"' in choice
    assert 'id="classification"' not in email  # contact details are always personal data
    assert 'id="minimum"' in number
    assert '<option value="pii" selected>' in number


async def test_a_proposed_not_personal_data_is_still_confirmed_by_the_administrator(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await post_form(site, PAGE, {**TOPIC, "confirm_not_pii": ""})

    assert refused.status_code == 422
    assert 'id="options"' in refused.text  # the form of the chosen kind stays
    assert '<a href="#confirm_not_pii">' in refused.text


async def test_an_always_personal_kind_is_added_without_a_classification(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    contact = {
        "key": "",
        "type": "email",
        "label_nl": "Mailadres van de partner",
        "label_en": "Partner email",
    }

    created = await post_form(site, PAGE, contact)

    assert created.headers["location"] == f"{PAGE}?done=created"
    row = (await site.client.get(PAGE)).text.split("Mailadres van de partner")[1].split("</tr>")[0]
    assert "chip--warning" in row  # tagged as personal data without being asked


async def test_refusals_are_linked_to_their_inputs(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    no_confirm = await post_form(site, PAGE, {**TOPIC, "confirm_not_pii": ""})
    forbidden = await post_form(site, PAGE, {**CUSTOMER, "key": "ssn"})
    bad_options = await post_form(site, PAGE, {**TOPIC, "options": "rent; Huur"})
    no_english = await post_form(site, PAGE, {**CUSTOMER, "label_en": ""})

    assert {r.status_code for r in (no_confirm, forbidden, bad_options, no_english)} == {422}
    assert '<a href="#confirm_not_pii">Bevestig' in no_confirm.text
    assert '<a href="#key">Dit veld vraagt om gegevens die we niet mogen vragen' in forbidden.text
    assert '<a href="#options">Geef 1 tot 30 keuzes' in bad_options.text
    assert '<a href="#label_nl">Vul de vraag in het Nederlands en het Engels in' in no_english.text
    assert 'value="Klantnummer"' in no_english.text  # what was typed stays


async def test_a_field_is_edited_moved_and_retired(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await post_form(site, PAGE, CUSTOMER)
    await post_form(site, PAGE, TOPIC)

    edit = (await site.client.get(f"{PAGE}/topic")).text
    assert "rent; Huur; Rent\ncare; Zorg; Care</textarea>" in edit
    assert 'name="type"' not in edit  # key and type are fixed
    saved = await post_form(site, f"{PAGE}/topic", {**TOPIC, "label_nl": "Waarover?"})
    assert saved.headers["location"] == f"{PAGE}?done=saved"
    unchanged = await post_form(site, f"{PAGE}/topic", {**TOPIC, "label_nl": "Waarover?"})
    assert unchanged.headers["location"] == f"{PAGE}?done=unchanged"
    refused = await post_form(site, f"{PAGE}/topic", {**TOPIC, "label_en": ""})
    assert refused.status_code == 422

    await post_form(site, f"{PAGE}/topic/move", {"direction": "up"})
    page = (await site.client.get(PAGE)).text
    assert page.index("Waarover?") < page.index("Klantnummer")

    retired = await post_form(site, f"{PAGE}/customer_no/retire", {})
    assert retired.headers["location"] == f"{PAGE}?done=field_retired#fields"
    page = (await site.client.get(PAGE)).text
    assert "Uitgezet (alleen nog bij eerdere afspraken): Klantnummer" in page
    again = await post_form(site, PAGE, CUSTOMER)
    assert '<a href="#key">Kies een sleutel' in again.text


async def test_a_group_admin_cannot_open_the_form(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    assert (await site.client.get(PAGE)).status_code == 403


async def test_the_booking_page_shows_the_answers_with_their_labels(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await post_form(site, PAGE, CUSTOMER)
    await post_form(site, PAGE, TOPIC)
    receipt = await BookingService(
        site_uow(site), site.clock, TEST_TOKEN_KEY, treatment_buffer=0
    ).book(
        group_slug="demo",
        start=FRIDAY_0900,
        contact=replace(ADA, answers={"customer_no": "A-12", "topic": "care"}),
        locale="nl",
    )

    text = (await site.client.get(f"/admin/groups/demo/bookings/{receipt.booking.id}")).text

    assert "<dt>Klantnummer</dt><dd>A-12</dd>" in text
    assert "<dt>Onderwerp</dt><dd>Zorg</dd>" in text
