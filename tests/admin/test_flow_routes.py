"""The flows of a service (ADR 0121 step 2d): list, editor, checklist and the lifecycle actions."""

import re

import pytest

from tests.support.admin_site import ORG_ADMIN, SAME_ORIGIN, Site, audit_log
from tests.support.flow_pages import BASE, act, create, form_data, save
from tests.support.pool_flows import shut_gate
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


async def test_a_flow_is_created_edited_published_and_audited_with_codes_only(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    shut_gate(monkeypatch)  # explicitly: the default opens with G10
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")

    created = await create(site, capacity_pool_id=str(pool), default_appointment="yes")

    assert created.status_code == 303
    assert created.headers["location"] == f"{BASE}/advies?done=flow_created"
    editor = (await site.client.get(f"{BASE}/advies")).text
    assert 'value="Samen bespreken"' in editor
    assert "Concept opslaan" in editor
    assert "Het concept is volledig, op onderdelen na die nog volgen" in editor
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
    assert readout.count("Eerste actie voor een medewerker:") == 1
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


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("", "Vul een code in."),
        ("   ", "Vul een code in."),
        ("New", "Gebruik alleen kleine letters, cijfers en streepjes"),
        ("new", "De code “new” is gereserveerd en kan niet worden gebruikt."),
        ("a b", "Gebruik alleen kleine letters, cijfers en streepjes"),
        ("ABC", "Gebruik alleen kleine letters, cijfers en streepjes"),
        ("-abc", "Gebruik alleen kleine letters, cijfers en streepjes"),
        ("a" * 41, "hoogstens 40 tekens"),
        ("appointment", "Deze code wordt al gebruikt"),
        ("a.b", "Gebruik alleen kleine letters, cijfers en streepjes"),
    ],
)
async def test_every_refused_flow_code_is_a_linked_field_error_with_its_own_message(
    site: Site, code: str, message: str
) -> None:
    await site.login_as(ORG_ADMIN)

    response = await create(site, code)

    assert response.status_code == 422
    shown = re.search(r'id="flow_code-error"[^>]*>(.*?)</p>', response.text, re.DOTALL)
    assert shown is not None
    assert message in shown.group(1)
    assert ("gereserveerd" in shown.group(1)) is (code == "new")
    assert 'href="#flow_code"' in response.text


async def test_the_refused_flow_codes_answer_in_english_too(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    data = {"": "Enter a code.", "new": "reserved", "a b": "Use only lower-case letters"}
    for code, message in data.items():
        response = await site.client.post(
            f"{BASE}?lang=en", data=form_data(flow_code=code), headers=SAME_ORIGIN
        )
        shown = re.search(r'id="flow_code-error"[^>]*>(.*?)</p>', response.text, re.DOTALL)
        assert response.status_code == 422
        assert shown is not None
        assert message in shown.group(1)
        assert ("reserved" in shown.group(1)) is (code == "new")
