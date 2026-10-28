"""Contact block screen (ADR 0037 §2): rows, preview, refusals linked to their input, audit."""

import httpx

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site
from tests.admin.test_admin_flow import audit_events

PAGE = "/admin/organisations/demo/contacts"


async def _save(site: Site, **data: str) -> httpx.Response:
    return await site.client.post(PAGE, data=data, headers=SAME_ORIGIN)


async def test_the_closure_page_links_to_the_contact_block(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    closure = (await site.client.get("/admin/organisations/demo/closure")).text

    assert f'href="{PAGE}"' in closure
    assert "Nog geen contactalternatieven ingesteld." in closure


async def test_six_empty_rows_and_a_public_warning_are_shown(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(PAGE)).text

    assert page.count("<legend>Contactmogelijkheid ") == 6
    assert 'name="contact_6_hours_en"' in page
    assert "Deze gegevens zijn openbaar." in page
    assert 'aria-describedby="contact_1_value-hint"' in page


async def test_options_are_saved_previewed_and_audited_with_counts(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _save(
        site,
        heading_nl="Bereik ons anders",
        contact_1_kind="phone",
        contact_1_value="14 020",
        contact_1_hours_nl="ma-vr 9:00-17:00",
        contact_2_kind="website",
        contact_2_value="https://www.gemeente.example/contact",
        contact_2_label_nl="Contactformulier",
        contact_4_kind="address",
        contact_4_value="Stadhuis\r\nAmstel 1\r\n1011 PN Amsterdam",
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=saved"
    page = (await site.client.get(response.headers["location"])).text
    assert "Bereik ons anders" in page
    assert '<a class="contact-block__link" href="tel:14020">14 020</a>' in page
    assert 'href="https://www.gemeente.example/contact">www.gemeente.example/contact</a>' in page
    assert "Contactformulier" in page
    assert "Stadhuis<br>Amstel 1<br>1011 PN Amsterdam" in page
    # saved in order: row 4 became the third option, and the form shows it there
    assert 'name="contact_3_kind"' in page
    assert (
        "sub-org",
        "organisation.closure",
        "success",
        "",  # the detail holds counts per kind (test_contact_service), never values
    ) in [(a, b, str(c), d) for a, b, c, d in await audit_events(site)]
    closure = (await site.client.get("/admin/organisations/demo/closure")).text
    assert "3 contactmogelijkheden ingesteld." in closure

    again = await _save(
        site,
        heading_nl="Bereik ons anders",
        contact_1_kind="phone",
        contact_1_value="14  020",
        contact_1_hours_nl="ma-vr 9:00-17:00",
        contact_2_kind="website",
        contact_2_value="https://www.gemeente.example/contact",
        contact_2_label_nl="Contactformulier",
        contact_3_kind="address",
        contact_3_value="Stadhuis\nAmstel 1\n1011 PN Amsterdam",
    )
    assert again.headers["location"] == f"{PAGE}?done=unchanged"

    removed = await _save(site)
    assert removed.headers["location"] == f"{PAGE}?done=saved"
    assert "Nog geen contactalternatieven ingesteld." in (await site.client.get(PAGE)).text


async def test_a_javascript_link_is_refused_with_a_message_linked_to_its_row(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _save(site, contact_2_kind="chat", contact_2_value="javascript:alert(1)")

    assert response.status_code == 422
    assert 'href="#contact_2_value"' in response.text
    assert "contactmogelijkheid 2 een link in die met https:// begint" in response.text
    assert 'aria-describedby="contact_2_value-hint contact_2_value-error"' in response.text
    assert "javascript:alert(1)</textarea>" in response.text  # kept for correction, escaped
    assert "Nog geen contactalternatieven ingesteld." in response.text  # nothing stored


async def test_details_without_a_kind_are_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _save(site, contact_5_value="14 020")

    assert response.status_code == 422
    assert 'href="#contact_5_kind"' in response.text
    assert "Kies bij contactmogelijkheid 5 een soort." in response.text


async def test_a_label_and_a_heading_that_are_too_long_are_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    label = await _save(
        site, contact_1_kind="phone", contact_1_value="14 020", contact_1_label_en="x" * 81
    )
    heading = await _save(
        site, heading_en="x" * 121, contact_1_kind="phone", contact_1_value="14 020"
    )

    assert 'href="#contact_1_label_nl"' in label.text
    assert 'href="#heading_nl"' in heading.text
    assert "De kop mag maximaal 120 tekens" in heading.text


async def test_group_admins_cannot_open_the_page(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    assert (await site.client.get(PAGE)).status_code == 403
    assert (await _save(site, contact_1_kind="phone", contact_1_value="14 020")).status_code == 403


async def test_cross_site_posts_are_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        PAGE,
        data={"contact_1_kind": "phone", "contact_1_value": "14 020"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
