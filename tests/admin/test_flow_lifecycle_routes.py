"""The flows of a service (ADR 0121 step 2d): refusals, the checklist and the lifecycle actions."""

import pytest

from app.domain import service_flows
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, Site
from tests.admin.test_flow_routes import BASE, act, create, form_data, save
from tests.support.admin_site import audit_log
from tests.support.flow_seed import add_subjects
from tests.support.skill_seed import add_pool, add_skill


async def test_the_page_speaks_english_with_the_names_of_both_languages(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    page = (await site.client.get(BASE, params={"lang": "en"})).text
    editor = (await site.client.get(f"{BASE}/advies", params={"lang": "en"})).text

    assert "Flows of this service" in page
    assert "This service&#39;s existing way of working." in page
    assert "Own staffing (existing)" in page
    row = page.split("<code>advies</code>")[0].rsplit("<tr>", 1)[1]
    assert "Discuss together" in row
    assert "Samen bespreken" in row  # the other language underneath
    assert "This flow is not ready to use" in editor
    assert "of 6 points in order." in editor
    assert "Save draft" in editor
    assert "Participants see this name. The name does not determine how the flow works." in editor
    assert "These follow the saved contact channel. Save the draft after a change." in editor


async def test_the_list_marks_a_published_flow_with_a_draft_in_progress(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)
    await act(site, "publish")

    before = (await site.client.get(BASE)).text
    await act(site, "prepare")
    after = (await site.client.get(BASE)).text

    assert "Concept in bewerking" not in before
    assert "Concept in bewerking" in after


async def test_archived_flows_are_behind_the_filter_like_the_skills(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_flows, "RETENTION_BUILT", True)
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await act(site, "publish")
    await act(site, "activate")
    await act(site, "archive")

    open_ = (await site.client.get(BASE)).text
    archived = (await site.client.get(BASE, params={"flows": "archived"})).text

    assert "<code>advies</code>" not in open_
    assert "<code>advies</code>" in archived
    assert "Gearchiveerde flows" in archived
    assert 'name="flows" value="archived" checked' in archived


async def test_a_refused_new_flow_shows_the_errors_linked_to_their_inputs(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await create(site, code="Not A Code")

    assert response.status_code == 422
    assert 'href="#flow_code"' in response.text
    assert 'aria-describedby="flow_code-hint flow_code-error"' in response.text
    assert 'value="Not A Code"' in response.text  # kept for correction
    assert 'value="Samen bespreken"' in response.text
    labels = await create(site, label_en="")
    assert labels.status_code == 422
    assert 'href="#label_nl"' in labels.text
    assert "Vul de naam in het Nederlands en in het Engels in" in labels.text
    length = await create(site, duration_minutes="7")
    assert 'href="#duration_minutes"' in length.text
    assert 'aria-describedby="duration_minutes-hint duration_minutes-error"' in length.text
    pool = await create(site, capacity_pool_id="999")
    assert 'href="#capacity_pool_id"' in pool.text
    assert not [e for e in await audit_log(site) if e.action == "group.edit"]


async def test_a_pool_of_another_organisation_is_an_error_of_its_input_not_a_missing_page(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    their_pool = await add_pool(site, "Theirs", organisation="andere")

    for pool in ("999", str(their_pool)):
        response = await create(site, capacity_pool_id=pool)
        assert response.status_code == 422, pool
        assert "Deze keuze bestaat niet meer" in response.text


async def test_the_reserved_and_the_taken_codes_are_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    taken = await create(site)
    reserved = await create(site, code="new")

    assert taken.status_code == reserved.status_code == 422
    assert "Deze code wordt al gebruikt door een flow van deze dienst" in taken.text
    assert 'href="#flow_code"' in reserved.text


async def test_a_default_for_a_path_the_flow_does_not_offer_is_a_field_error(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await create(site, default_on_demand="yes")

    assert response.status_code == 422
    assert 'href="#default_appointment"' in response.text
    assert "alleen de standaard zijn voor een aanmeldweg die hij zelf aanbiedt" in response.text


async def test_the_subjects_are_offered_with_extra_skills_and_read_back(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract", "aanvraag": "Aanvraag"})
    general = await add_skill(site, "general-advice", "Algemeen advies", "General advice")
    english = await add_skill(site, "language-en", "Engels", "English")
    pool = await add_pool(site, "Advies", (general, english))

    created = await create(
        site,
        capacity_pool_id=str(pool),
        **{
            f"skill-{general}": "on",
            "offer_contract": "yes",
            f"extra_contract_{english}": "on",
            f"extra_aanvraag_{english}": "on",  # not offered: ignored
        },
    )

    assert created.status_code == 303
    page = (await site.client.get(f"{BASE}/advies")).text
    assert f'id="skill-{general}" name="skill-{general}" value="on" checked' in page
    assert f'id="skill-{english}" name="skill-{english}" value="on">' in page
    assert 'id="offer_contract" name="offer_contract" value="yes" checked' in page
    assert 'id="offer_aanvraag" name="offer_aanvraag" value="yes">' in page
    assert f'name="extra_contract_{english}" value="on" checked' in page
    assert f'name="extra_aanvraag_{english}" value="on">' in page
    assert "5 van 6 punten in orde." in page  # the pool guarantees what the flow needs


async def test_a_pool_that_does_not_guarantee_the_skills_is_a_missing_point_on_the_checklist(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    general = await add_skill(site, "general-advice", "Algemeen advies", "General advice")
    pool = await add_pool(site, "Leeg")
    await create(site, capacity_pool_id=str(pool), **{f"skill-{general}": "on"})

    page = (await site.client.get(f"{BASE}/advies")).text

    assert "De capaciteitspool garandeert niet alle vaardigheden" in page
    assert "4 van 6 punten in orde." in page


async def test_the_checklist_says_every_state_in_words_and_follows_for_what_is_not_built(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, interaction_kind="video")

    page = (await site.client.get(f"{BASE}/advies")).text

    checklist = page.split('<ol class="checklist">')[1].split("</ol>")[0]
    assert checklist.count('class="checklist__item"') == 6
    assert checklist.count("<span>Volgt</span>") == 2  # video and retention are not built
    assert checklist.count("<span>Ontbreekt</span>") == 2  # no pool: capacity and skills
    assert checklist.count("<span>In orde</span>") == 2
    assert "Dit onderdeel van OpenPlanner volgt in een latere versie." in checklist
    assert "Kies een capaciteitspool." in checklist


async def test_activation_is_a_disabled_button_with_its_explanation_and_a_refusal_with_the_list(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await act(site, "publish")

    page = (await site.client.get(f"{BASE}/advies")).text
    refused = await act(site, "activate")

    assert "Activeren kan zodra alle punten in orde zijn." in page
    assert 'disabled aria-describedby="activate-hint">Flow activeren' in page
    assert refused.status_code == 409
    assert "De flow is niet geactiveerd" in refused.text
    assert "<span>Volgt</span>" in refused.text
    assert "Beoordeeld op de gepubliceerde revisie (1)." in refused.text
    assert not [e for e in await audit_log(site) if "activated" in e.detail]


async def test_a_flow_that_is_ready_is_activated_with_the_constants_patched(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_flows, "RETENTION_BUILT", True)
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await act(site, "publish")

    page = (await site.client.get(f"{BASE}/advies")).text
    activated = await act(site, "activate")

    assert "Deze flow is klaar voor gebruik" in page
    assert 'disabled aria-describedby="activate-hint"' not in page
    assert activated.status_code == 303
    assert activated.headers["location"] == f"{BASE}/advies?done=flow_activated"
    after = (await site.client.get(f"{BASE}/advies", params={"done": "flow_activated"})).text
    assert "De flow is geactiveerd." in after
    assert '<span class="chip">Actief</span>' in after
    assert f'href="{BASE}/advies/archive"' in after
    details = [e.detail for e in await audit_log(site) if e.action == "group.edit"]
    assert details[-1] == "flow.activated,flow:advies,revision:1"


async def test_discarding_asks_for_confirmation_and_a_new_flow_disappears(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    confirm = await site.client.get(f"{BASE}/advies/discard")
    gone = await act(site, "discard")

    assert confirm.status_code == 200
    assert "De flow is nog nooit gepubliceerd en verdwijnt uit de lijst." in confirm.text
    assert f'action="{BASE}/advies/discard"' in confirm.text
    assert gone.status_code == 303
    assert gone.headers["location"] == f"{BASE}?done=flow_discarded_gone"
    assert "<code>advies</code>" not in (await site.client.get(BASE)).text
    assert (await site.client.get(f"{BASE}/advies")).status_code == 404


async def test_discarding_the_draft_of_a_published_flow_keeps_the_published_revision(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)
    await act(site, "publish")
    await act(site, "prepare")

    confirm = (await site.client.get(f"{BASE}/advies/discard")).text
    done = await act(site, "discard")
    page = (await site.client.get(f"{BASE}/advies")).text

    assert "De gepubliceerde revisie blijft zoals ze is." in confirm
    assert done.headers["location"] == f"{BASE}/advies?done=flow_discarded"
    assert "Gepubliceerde revisie 1" in page
    assert "Concept in bewerking" not in page


async def test_a_flow_in_draft_state_cannot_be_archived_and_the_page_says_discard(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)
    await act(site, "publish")

    page = await site.client.get(f"{BASE}/advies/archive")
    refused = await act(site, "archive")

    assert page.status_code == 200
    assert "Alleen een actieve flow kan worden gearchiveerd." in page.text
    assert "Flow archiveren</button>" not in page.text
    assert refused.status_code == 422
    assert "Dat kan niet in de huidige status van deze flow." in refused.text


async def test_archiving_an_active_flow_says_what_happens(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_flows, "RETENTION_BUILT", True)
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await act(site, "publish")
    await act(site, "activate")

    page = (await site.client.get(f"{BASE}/advies/archive")).text
    done = await act(site, "archive")

    assert "Er komen geen nieuwe verzoeken meer binnen via deze flow." in page
    assert "Bestaande verzoeken houden de flow waarmee ze zijn gemaakt." in page
    assert done.headers["location"] == f"{BASE}?done=flow_archived"
    archived = (await site.client.get(f"{BASE}/advies")).text
    assert "Gearchiveerd" in archived
    assert "Concept opslaan" not in archived  # read-only


async def test_a_published_revision_is_read_only_until_a_new_one_is_prepared(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, default_appointment="yes")
    await act(site, "publish")

    read_only = (await site.client.get(f"{BASE}/advies")).text
    refused = await save(site, label_nl="Anders")
    await act(site, "prepare")
    editor = (await site.client.get(f"{BASE}/advies")).text

    assert "Concept opslaan" not in read_only
    assert "Nieuwe revisie voorbereiden" in read_only
    assert refused.status_code == 422
    assert "Concept opslaan" in editor
    assert 'name="default_appointment" value="yes" checked disabled' in editor
    assert "Zodra een flow een gepubliceerde revisie heeft, staan de standaardwegen vast." in editor
    assert (await save(site, label_nl="Anders")).status_code == 303
    assert 'value="Anders"' in (await site.client.get(f"{BASE}/advies")).text


async def test_a_flow_that_was_never_published_may_still_change_its_default_paths(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, default_appointment="yes")

    await save(site)
    page = (await site.client.get(f"{BASE}/advies")).text

    assert 'name="default_appointment" value="yes">' in page


async def test_every_edit_publish_discard_activate_and_archive_of_a_legacy_flow_is_404(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    for method, path in (
        ("GET", f"{BASE}/appointment"),
        ("POST", f"{BASE}/appointment"),
        ("POST", f"{BASE}/appointment/prepare"),
        ("POST", f"{BASE}/appointment/publish"),
        ("GET", f"{BASE}/appointment/discard"),
        ("POST", f"{BASE}/appointment/discard"),
        ("POST", f"{BASE}/appointment/activate"),
        ("GET", f"{BASE}/appointment/archive"),
        ("POST", f"{BASE}/appointment/archive"),
        ("GET", f"{BASE}/nope"),
    ):
        response = await site.client.request(method, path, data=form_data(), headers=SAME_ORIGIN)
        assert response.status_code == 404, (method, path)


async def test_two_flows_with_arbitrary_names_and_one_configuration_read_the_same(
    site: Site,
) -> None:
    """A01 at this level: a name decides nothing, not the read-outs nor the checklist."""
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    for code, nl, en in (("een", "zzz", "yyy"), ("twee", "Balie 7", "Desk 7")):
        await create(
            site,
            code,
            label_nl=nl,
            label_en=en,
            capacity_pool_id=str(pool),
            interaction_kind="telephone",
        )

    def judged(page: str) -> tuple[str, str]:
        readout = page.split('<div id="kind-readout">')[1].split("</div>")[0]
        checklist = page.split('<ol class="checklist">')[1].split("</ol>")[0]
        return readout, checklist

    first = judged((await site.client.get(f"{BASE}/een")).text)
    second = judged((await site.client.get(f"{BASE}/twee")).text)

    assert first == second
    assert "de deelnemer bellen" in first[0]


async def test_the_pool_text_links_to_skills_and_capacity_for_a_reader_of_the_organisation(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(f"{BASE}/new")).text

    assert "Capaciteitspools beheert de organisatiebeheerder." in page
    assert 'href="/admin/organisations/demo/skills"' in page
    assert "Een nieuwe flow put uit een capaciteitspool van de organisatie" in page


async def test_crafted_field_names_and_values_never_cause_a_server_error(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    skill = await add_skill(site)
    pool = await add_pool(site, "Advies", (skill,))
    crafted = {
        "skill-²": "on",
        "skill-٣": "on",
        "skill--1": "on",
        "extra_contract_²": "on",
        "extra_contract_٣": "on",
        "extra_contract_-1": "on",
        "extra_nope_1": "on",
        "extra__1": "on",
        "offer_nope": "yes",
        "offer_²": "yes",
        "duration_minutes": "²",
        "capacity_pool_id": "٣",
        "interaction_kind": "teleport",
        "handling_action": "²",
        "dispatch": "pigeon",
        "completion": "",
    }
    ticked = {f"skill-{n}": "on" for n in range(1, 400)}

    for data in (
        crafted,
        {**crafted, "capacity_pool_id": str(pool), "duration_minutes": "30"},
        ticked,
    ):
        for path in (BASE, f"{BASE}/nope"):
            response = await site.client.post(
                path, data=form_data(flow_code="x", **data), headers=SAME_ORIGIN
            )
            assert response.status_code in (404, 422, 303), (path, response.status_code)
    assert (await site.client.get(f"{BASE}/new")).status_code == 200


async def test_too_many_ticked_skills_are_refused_before_anything_is_looked_up(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    ticked = {f"skill-{n}": "on" for n in range(1, 205)}  # the limit of skills is 100: cap 200

    response = await create(site, **ticked)

    assert response.status_code == 422
    assert 'href="#required_skills"' in response.text
    assert not [e for e in await audit_log(site) if e.action == "group.edit"]


async def test_the_new_pages_have_no_script_and_no_inline_style(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    await create(site)
    await act(site, "publish")
    await act(site, "prepare")
    pages = [
        BASE,
        f"{BASE}/new",
        f"{BASE}/advies",
        f"{BASE}/advies/discard",
        f"{BASE}/advies/archive",
    ]

    for path in pages:
        for lang in ("nl", "en"):
            response = await site.client.get(path, params={"lang": lang})
            assert response.status_code == 200, path
            text = response.text.split("<main", 1)[1]
            assert "<script" not in text, path
            assert "style=" not in text, path


async def test_a_flow_with_an_archived_skill_keeps_it_shown_and_untickable(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    old = await add_skill(site, "oud", "Oud", "Old")
    pool = await add_pool(site, "Advies", (old,))
    await create(site, capacity_pool_id=str(pool), **{f"skill-{old}": "on"})
    await site.client.post(f"/admin/organisations/demo/skills/{old}/archive", headers=SAME_ORIGIN)

    page = (await site.client.get(f"{BASE}/advies")).text
    unticked = await save(site, capacity_pool_id=str(pool))

    assert f'id="skill-{old}" name="skill-{old}" value="on" checked' in page
    assert "Gearchiveerd" in page.split(f'id="skill-{old}"')[1].split("</div>")[0]
    assert unticked.status_code == 303
