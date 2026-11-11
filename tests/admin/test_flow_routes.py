"""The flows of a service (ADR 0121 step 2d): list, editor, checklist and the lifecycle actions."""

import pytest

from tests.admin.conftest import ORG_ADMIN, Site
from tests.support.admin_site import audit_log
from tests.support.flow_pages import BASE, act, create, save
from tests.support.skill_seed import add_pool


async def test_the_flows_sub_tab_lists_the_existing_flow_read_only(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(BASE)).text

    assert "Flows van deze dienst" in page
    assert "Een flow bepaalt hoe een verzoek wordt afgehandeld" in page
    assert "Bestaande werkwijze van deze dienst." in page
    assert "<code>appointment</code>" in page
    assert "Eigen bezetting (bestaand)" in page
    assert f'href="{BASE}/appointment"' not in page  # no edit link on a legacy flow
    assert f'href="{BASE}/new"' in page
    assert f'href="{BASE}"' in (await site.client.get("/admin/groups/demo/settings")).text


async def test_a_flow_is_created_edited_published_and_audited_with_codes_only(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")

    created = await create(site, capacity_pool_id=str(pool), default_appointment="yes")

    assert created.status_code == 303
    assert created.headers["location"] == f"{BASE}/advies?done=flow_created"
    editor = (await site.client.get(f"{BASE}/advies")).text
    assert 'value="Samen bespreken"' in editor
    assert "Concept opslaan" in editor
    assert "Het concept is nog niet volledig" in editor
    assert (await save(site, label_nl="Nieuwe naam", capacity_pool_id=str(pool))).status_code == 303
    assert (await act(site, "publish")).status_code == 303
    after = (await site.client.get(f"{BASE}/advies")).text
    assert "Gepubliceerde revisie 1" in after
    assert "Nieuwe revisie voorbereiden" in after
    details = [e.detail for e in await audit_log(site) if e.action == "group.edit"]
    assert details == [
        "flow.created,flow:advies,revision:1",
        "flow.draft_saved,flow:advies,revision:1",
        "flow.published,flow:advies,revision:1",
    ]
    assert "Nieuwe naam" not in repr(await audit_log(site))


@pytest.mark.parametrize("kind", ["telephone", "in_person", "none"])
async def test_the_read_outs_follow_the_saved_contact_channel(site: Site, kind: str) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, interaction_kind="none")

    await save(site, interaction_kind=kind)
    page = (await site.client.get(f"{BASE}/advies")).text

    expected = {
        "telephone": ("direct, zonder aparte melding", "de deelnemer bellen"),
        "in_person": ("wanneer de deelnemer zich meldt", "het verzoek bekijken"),
        "none": ("direct, zonder aparte melding", "het verzoek bekijken"),
    }[kind]
    for text in expected:
        assert text in page
    readout = page.split('<div id="kind-readout">')[1].split("</div>")[0]
    assert "Gereed voor afhandeling:" in readout
    assert readout.count("Eerste actie van de medewerker:") == 1
    assert " / " not in readout  # one action, never the list of the allowed ones
    assert ("handling_action" in page) is (kind == "in_person")
    if kind == "in_person":
        assert "een werkplek toewijzen (volgt)" in page
        assert "disabled" in page.split("een werkplek toewijzen (volgt)")[0].rsplit("<option", 1)[1]
        assert "Kies de eerste actie" in page


async def test_video_is_listed_to_follow_and_refused_like_every_other_unbuilt_option(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    created = await create(site, interaction_kind="video")
    await create(site, interaction_kind="none")

    saved = await save(site, interaction_kind="video")
    page = (await site.client.get(f"{BASE}/advies")).text

    assert created.status_code == saved.status_code == 422
    assert 'href="#interaction_kind"' in created.text
    radio = page.split('id="kind-video"')[1].split(">")[0]
    assert "disabled" in radio
    assert "Videogesprek (volgt)" in page


async def test_an_action_that_does_not_fit_the_posted_channel_is_replaced_and_the_page_says_so(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, interaction_kind="in_person")
    await save(site, interaction_kind="in_person", handling_action="view_request")

    saved = await save(site, interaction_kind="telephone", handling_action="view_request")
    refused = await save(
        site, interaction_kind="telephone", handling_action="view_request", label_en=""
    )
    page = (await site.client.get(f"{BASE}/advies")).text

    assert saved.status_code == 303
    assert saved.headers["location"] == f"{BASE}/advies?done=flow_saved_adjusted"
    assert "De eerste actie is aangepast aan het gekozen contactkanaal." in refused.text
    assert "de deelnemer bellen" in page  # the channel's own first action
    after = await site.client.get(saved.headers["location"])
    assert "De eerste actie is aangepast aan het gekozen contactkanaal." in after.text
    quiet = await save(site, interaction_kind="telephone", handling_action="call")
    assert quiet.headers["location"] == f"{BASE}/advies?done=flow_saved"
