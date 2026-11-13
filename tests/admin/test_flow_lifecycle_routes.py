"""The flows of a service (ADR 0121 step 2d): refusals, the checklist and the lifecycle actions."""

import re
from urllib.parse import parse_qsl, urlencode

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    BookingRow,
    CapacityPoolRow,
    GroupRow,
    ServiceFlowRow,
    SlotRow,
)
from app.admin import flow_views as views
from app.admin.forms import MAX_FORM_BYTES
from app.domain import service_flows
from app.domain.authorization import Scope
from app.domain.routing import GroupRouting, Subject
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import audit_log, site_app, site_uow
from tests.support.domain import NOW
from tests.support.flow_pages import BASE, act, create, create_ready, form_data, save
from tests.support.flow_seed import add_subjects
from tests.support.locales import nl_locale
from tests.support.skill_seed import add_pool, add_skill

SUPER_SCOPE = Scope(everything=True)


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
    assert "The draft is not complete yet" in editor
    assert "Not active yet" in editor
    assert "How does a request come in?" in editor
    assert "By appointment" in editor
    assert "of 6 points in order." in editor
    assert "Save draft" in editor
    assert "Participants see this name. The name does not determine how the flow works." in editor
    assert "These follow the saved contact channel. Save the draft after a change." in editor


async def test_the_list_marks_a_published_flow_with_a_draft_in_progress(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create_ready(site)
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
    monkeypatch.setattr(service_flows, "ROLLBACK_SAFE", True)  # ADR 0122: the gate too
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


async def test_the_subjects_are_offered_and_their_extra_skills_are_chosen_on_a_page_of_their_own(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract", "aanvraag": "Aanvraag"})
    general = await add_skill(site, "general-advice", "Algemeen advies", "General advice")
    english = await add_skill(site, "language-en", "Engels", "English")
    pool = await add_pool(site, "Advies", (general, english))

    created = await create(
        site,
        capacity_pool_id=str(pool),
        **{f"skill-{general}": "on", "offer_contract": "yes"},
    )

    assert created.status_code == 303
    page = (await site.client.get(f"{BASE}/advies")).text
    assert f'id="skill-{general}" name="skill-{general}" value="on" checked' in page
    assert 'id="offer_contract" name="offer_contract" value="yes" checked' in page
    assert 'id="offer_aanvraag" name="offer_aanvraag" value="yes">' in page
    assert "extra_contract_" not in page  # the main form carries no extra skills
    assert f'href="{BASE}/advies/subjects/contract"' in page
    assert f'href="{BASE}/advies/subjects/aanvraag"' not in page  # not offered
    assert "Extra vaardigheden kiezen" in page
    assert "verliest de extra vaardigheden die u ervoor koos" in page
    assert "verliest" not in page.split("<thead>")[1].split("</thead>")[0]  # not in a header cell
    form = await site.client.get(f"{BASE}/advies/subjects/contract")
    assert form.status_code == 200
    assert f'id="skill-{english}" name="skill-{english}" value="on">' in form.text
    saved = await site.client.post(
        f"{BASE}/advies/subjects/contract", data={f"skill-{english}": "on"}, headers=SAME_ORIGIN
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == f"{BASE}/advies?done=flow_subject_saved#subjects"
    page = (await site.client.get(f"{BASE}/advies")).text
    assert "5 van 6 punten in orde." in page  # the pool guarantees what the flow needs
    row = page.split('id="offer_contract"')[1].split("</tr>")[0]
    assert "Engels" in row
    again = await site.client.get(f"{BASE}/advies/subjects/contract")
    assert f'id="skill-{english}" name="skill-{english}" value="on" checked' in again.text
    details = [e.detail for e in await audit_log(site) if e.action == "group.edit"]
    assert details[-1] == "flow.draft_saved,flow:advies,revision:1"


async def test_saving_the_main_form_keeps_the_extra_skills_of_a_subject_that_stays_offered(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract", "aanvraag": "Aanvraag"})
    english = await add_skill(site, "language-en", "Engels", "English")
    pool = await add_pool(site, "Advies", (english,))
    await create(site, capacity_pool_id=str(pool), offer_contract="yes", offer_aanvraag="yes")
    for subject in ("contract", "aanvraag"):
        await site.client.post(
            f"{BASE}/advies/subjects/{subject}",
            data={f"skill-{english}": "on"},
            headers=SAME_ORIGIN,
        )

    await save(site, capacity_pool_id=str(pool), offer_contract="yes")
    page = (await site.client.get(f"{BASE}/advies")).text

    contract = page.split('id="offer_contract"')[1].split("</tr>")[0]
    aanvraag = page.split('id="offer_aanvraag"')[1].split("</tr>")[0]
    assert "Engels" in contract
    assert "Engels" not in aanvraag  # no longer offered: it lost its extra skills
    await save(site, capacity_pool_id=str(pool), offer_contract="yes", offer_aanvraag="yes")
    again = (await site.client.get(f"{BASE}/advies")).text
    assert "Engels" not in again.split('id="offer_aanvraag"')[1].split("</tr>")[0]


async def test_the_page_of_a_subject_is_missing_without_a_draft_or_for_a_subject_not_offered(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    await create_ready(site, offer_contract="yes")
    await act(site, "publish")

    for method in ("GET", "POST"):
        response = await site.client.request(
            method, f"{BASE}/advies/subjects/contract", headers=SAME_ORIGIN
        )
        assert response.status_code == 404, method
    await act(site, "prepare")
    assert (await site.client.get(f"{BASE}/advies/subjects/contract")).status_code == 200
    assert (await site.client.get(f"{BASE}/advies/subjects/nope")).status_code == 404


async def test_an_offered_subject_that_is_not_saved_yet_says_to_save_first(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    await create(site)

    refused = await save(site, offer_contract="yes", label_en="")  # ticked, but not saved

    assert refused.status_code == 422
    row = refused.text.split('id="offer_contract"')[1].split("</tr>")[0]
    assert "Sla het concept eerst op om extra vaardigheden te kiezen." in row
    assert "/subjects/contract" not in row


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


async def test_the_checklist_says_every_state_in_words_and_which_part_follows(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = service_flows.BUILT_DISPATCH
    monkeypatch.setattr(
        service_flows,
        "BUILT_DISPATCH",
        frozenset({*built, service_flows.DispatchKind.CONTACT_CENTER}),
    )
    await site.login_as(ORG_ADMIN)
    await create(site, dispatch="contact_center")
    monkeypatch.setattr(service_flows, "BUILT_DISPATCH", built)

    page = (await site.client.get(f"{BASE}/advies")).text

    checklist = page.split('<ol class="checklist">')[1].split("</ol>")[0]
    assert checklist.count('class="checklist__item"') == 6
    assert checklist.count("<span>Volgt</span>") == 1  # retention is not built
    # No pool (capacity and skills), and the service has no contact-center routing: something the
    # administrator can fix, named before what follows (ADR 0121 step 6). The dispatch sentence
    # of ``not_built_yet`` is pinned in ``tests/admin/test_flow_views.py``.
    assert checklist.count("<span>Ontbreekt</span>") == 3
    assert checklist.count("<span>In orde</span>") == 2
    assert "nog niet met een contactcenter verbonden" in checklist
    assert "Het automatisch afsluiten en verwijderen van gegevens" in checklist
    assert "Dit onderdeel van OpenPlanner volgt" not in checklist
    assert "Kies een capaciteitspool." in checklist
    assert "Het concept is nog niet volledig" in page


async def test_the_checklist_names_what_it_judges_and_where_it_stands_in_its_heading(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await create(site, "leeg")

    draft = (await site.client.get(f"{BASE}/advies")).text
    empty = (await site.client.get(f"{BASE}/leeg")).text
    await act(site, "publish")
    published = (await site.client.get(f"{BASE}/advies")).text
    await act(site, "prepare")
    both = (await site.client.get(f"{BASE}/advies")).text

    assert "Het concept is volledig, op onderdelen na die nog volgen" in draft
    assert "Punten die ‘Volgt’ tonen, kunt u zelf niet oplossen." in draft
    english = (await site.client.get(f"{BASE}/advies", params={"lang": "en"})).text
    assert "Points marked ‘To follow’ cannot be fixed by you." in english
    assert "Het concept is nog niet volledig" in empty
    assert "kunt u zelf niet oplossen" not in empty
    assert "De gepubliceerde revisie is volledig, op onderdelen na die nog volgen" in published
    assert "Het concept is volledig, op onderdelen na die nog volgen" in both
    assert "Beoordeeld op het opgeslagen concept (revisie 2)." in both


async def test_activation_has_no_disabled_button_but_says_why_not_and_a_refusal_lists_the_points(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await create_ready(site, "leeg")

    unpublished = (await site.client.get(f"{BASE}/advies")).text
    await act(site, "publish")
    await act(site, "publish", "leeg")
    async with AsyncSession(site_app(site).state.engine) as session, session.begin():
        await session.execute(
            update(CapacityPoolRow)
            .where(CapacityPoolRow.code == "pool-leeg")
            .values(archived_at=NOW)
        )
    page = (await site.client.get(f"{BASE}/advies")).text
    refused = await act(site, "activate")
    monkeypatch.setattr(service_flows, "RETENTION_BUILT", True)  # only the archived pool is left
    monkeypatch.setattr(service_flows, "ROLLBACK_SAFE", True)  # ADR 0122: the gate too
    other = (await site.client.get(f"{BASE}/leeg")).text

    assert "Publiceer eerst het concept." in unpublished
    assert "Nieuwe flows kunnen in deze versie nog niet worden geactiveerd." in page
    assert "Activeren kan zodra de gepubliceerde revisie op alle punten in orde is." in other
    for text in (unpublished, page, other):
        assert "disabled" not in text.split('id="activate-heading"')[1].split("</section>")[0]
        assert "Flow activeren</button>" not in text
    assert refused.status_code == 409
    assert "De flow is niet geactiveerd" in refused.text
    assert "<span>Volgt</span>" in refused.text
    assert "Beoordeeld op de gepubliceerde revisie (1)." in refused.text
    assert not [e for e in await audit_log(site) if "activated" in e.detail]


async def test_a_flow_that_is_ready_is_activated_with_the_constants_patched(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_flows, "RETENTION_BUILT", True)
    monkeypatch.setattr(service_flows, "ROLLBACK_SAFE", True)  # ADR 0122: the gate too
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await act(site, "publish")

    page = (await site.client.get(f"{BASE}/advies")).text
    activated = await act(site, "activate")

    assert "De gepubliceerde revisie is klaar voor gebruik" in page
    assert "Flow activeren</button>" in page
    assert activated.status_code == 303
    assert activated.headers["location"] == f"{BASE}/advies?done=flow_activated"
    after = (await site.client.get(f"{BASE}/advies", params={"done": "flow_activated"})).text
    assert "De flow is geactiveerd." in after
    assert '<span class="chip">Actief</span>' in after
    assert f'href="{BASE}/advies/archive"' in after
    details = [e.detail for e in await audit_log(site) if e.action == "group.edit"]
    assert details[-1] == "flow.activated,flow:advies,revision:1"


async def test_a_flow_never_active_is_deleted_after_a_confirmation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    editor = (await site.client.get(f"{BASE}/advies")).text
    confirm = await site.client.get(f"{BASE}/advies/delete")
    gone = await act(site, "delete")

    assert f'href="{BASE}/advies/delete"' in editor
    assert f'href="{BASE}/advies/discard"' not in editor  # a flow never published: only delete
    assert f'href="{BASE}/advies/archive"' not in editor
    assert confirm.status_code == 200
    assert (
        "Deze flow is nog nooit actief geweest. Verwijderen haalt de flow en alle revisies weg."
        in (confirm.text)
    )
    assert "Dit kan niet ongedaan worden gemaakt." in confirm.text
    assert "Samen bespreken (advies)" in confirm.text
    assert f'action="{BASE}/advies/delete"' in confirm.text
    assert "Flow verwijderen</button>" in confirm.text
    assert gone.status_code == 303
    assert gone.headers["location"] == f"{BASE}?done=flow_deleted"
    assert "De flow is verwijderd." in (await site.client.get(gone.headers["location"])).text
    assert "<code>advies</code>" not in (await site.client.get(BASE)).text
    assert (await site.client.get(f"{BASE}/advies")).status_code == 404
    details = [e.detail for e in await audit_log(site) if e.action == "group.edit"]
    assert details[-1] == "flow.deleted,flow:advies"


async def test_a_published_flow_never_active_offers_delete_and_discard_of_its_draft(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create_ready(site)
    await act(site, "publish")
    await act(site, "prepare")
    await save(site, label_nl="Nieuwe naam")

    editor = (await site.client.get(f"{BASE}/advies")).text
    delete = (await site.client.get(f"{BASE}/advies/delete")).text
    discard = (await site.client.get(f"{BASE}/advies/discard")).text

    assert f'href="{BASE}/advies/delete"' in editor
    assert f'href="{BASE}/advies/discard"' in editor
    assert f'href="{BASE}/advies/archive"' not in editor
    assert "Samen bespreken (advies)" in delete  # the name the list shows: the published one
    assert "Nieuwe naam (advies)" in discard  # the draft's name, the draft that goes


async def test_discarding_a_flow_never_published_is_refused_and_the_page_says_delete(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    page = await site.client.get(f"{BASE}/advies/discard")
    refused = await act(site, "discard")

    assert "Concept weggooien</button>" not in page.text
    assert "verwijdert u" in page.text
    assert refused.status_code == 422
    assert (await site.client.get(f"{BASE}/advies")).status_code == 200  # the flow is still there


async def test_an_active_flow_is_not_deleted_and_the_page_says_archive(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    await activated(site, monkeypatch)

    page = await site.client.get(f"{BASE}/advies/delete")
    refused = await act(site, "delete")
    editor = (await site.client.get(f"{BASE}/advies")).text

    assert "Flow verwijderen</button>" not in page.text
    assert "archiveert u" in page.text
    assert refused.status_code == 422
    assert f'href="{BASE}/advies/delete"' not in editor
    assert f'href="{BASE}/advies/archive"' in editor


async def test_a_flow_a_booking_refers_to_is_not_deleted(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)
    async with AsyncSession(site_app(site).state.engine) as session, session.begin():
        flow = await session.scalar(select(ServiceFlowRow).where(ServiceFlowRow.code == "advies"))
        group = await session.scalar(select(GroupRow).where(GroupRow.slug == "demo"))
        assert flow is not None
        assert group is not None
        slot = SlotRow(group_id=group.id, start=NOW, end=NOW, capacity_snapshot=1)
        session.add(slot)
        await session.flush()
        session.add(
            BookingRow(
                slot_id=slot.id,
                group_id=group.id,
                status="cancelled",
                locale="nl",
                management_token_hash="c" * 64,
                public_ref="ref-c",
                created_at=NOW,
                flow_id=flow.id,
                flow_revision=1,
            )
        )

    refused = await act(site, "delete")

    assert refused.status_code == 422
    assert "Een boeking verwijst nog naar deze flow" in refused.text
    assert (await site.client.get(f"{BASE}/advies")).status_code == 200


async def test_discarding_the_draft_of_a_published_flow_keeps_the_published_revision(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create_ready(site)
    await act(site, "publish")
    await act(site, "prepare")

    confirm = (await site.client.get(f"{BASE}/advies/discard")).text
    done = await act(site, "discard")
    page = (await site.client.get(f"{BASE}/advies")).text

    assert "De gepubliceerde revisie blijft zoals ze is." in confirm
    assert done.headers["location"] == f"{BASE}/advies?done=flow_discarded"
    assert "Gepubliceerde revisie 1" in page
    assert "Concept in bewerking" not in page


async def activated(site: Site, monkeypatch: pytest.MonkeyPatch) -> int:
    """Log in, store the flow ``advies``, publish and activate it; return its pool."""
    monkeypatch.setattr(service_flows, "RETENTION_BUILT", True)
    monkeypatch.setattr(service_flows, "ROLLBACK_SAFE", True)  # ADR 0122: the gate too
    await site.login_as(ORG_ADMIN)
    pool = await add_pool(site, "Advies")
    await create(site, capacity_pool_id=str(pool))
    await act(site, "publish")
    await act(site, "activate")
    return pool


async def test_an_active_flow_is_archived_with_its_draft(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    await activated(site, monkeypatch)
    await act(site, "prepare")

    before = (await site.client.get(f"{BASE}/advies")).text
    page = await site.client.get(f"{BASE}/advies/archive")
    done = await act(site, "archive")

    assert page.status_code == 200
    assert "Concept in bewerking" in before
    assert "Een concept van de flow wordt weggegooid." in page.text
    assert "Een gearchiveerde flow kan in het portaal niet opnieuw worden geactiveerd." in page.text
    assert "Samen bespreken (advies)" in page.text
    assert "Flow archiveren</button>" in page.text
    assert done.headers["location"] == f"{BASE}?done=flow_archived"
    details = [e.detail for e in await audit_log(site) if e.action == "group.edit"]
    assert details[-2:] == [
        "flow.draft_discarded,flow:advies,revision:2",
        "flow.archived,flow:advies,revision:1",
    ]
    archived = (await site.client.get(f"{BASE}/advies")).text
    assert "Gearchiveerd" in archived
    assert "Concept weggooien" not in archived


async def test_a_flow_never_active_cannot_be_archived_and_the_page_says_delete(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create_ready(site)
    await act(site, "publish")

    page = await site.client.get(f"{BASE}/advies/archive")
    refused = await act(site, "archive")

    assert page.status_code == 200
    assert "Een flow die nog nooit actief is geweest, verwijdert u." in page.text
    assert "Flow archiveren</button>" not in page.text
    assert refused.status_code == 422
    assert "Dat kan niet in de huidige status van deze flow." in refused.text


async def test_the_draft_of_an_archived_flow_is_not_discarded_and_the_page_says_so(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    await activated(site, monkeypatch)
    await act(site, "archive")

    page = await site.client.get(f"{BASE}/advies/discard")
    refused = await act(site, "discard")

    assert page.status_code == 200
    assert "Alleen het concept van een flow met een gepubliceerde revisie" in page.text
    assert "Concept weggooien</button>" not in page.text
    assert refused.status_code == 422


async def test_archiving_an_active_flow_says_what_happens(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_flows, "RETENTION_BUILT", True)
    monkeypatch.setattr(service_flows, "ROLLBACK_SAFE", True)  # ADR 0122: the gate too
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
    await create_ready(site, default_appointment="yes")
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
        ("GET", f"{BASE}/appointment/delete"),
        ("POST", f"{BASE}/appointment/delete"),
        ("GET", f"{BASE}/nope"),
    ):
        response = await site.client.request(method, path, data=form_data(), headers=SAME_ORIGIN)
        assert response.status_code == 404, (method, path)


async def test_two_flows_with_arbitrary_names_and_one_configuration_read_the_same(
    site: Site,
) -> None:
    """A01 at this level: a name decides nothing, not the read-outs nor the checklist items."""
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

    async def judged(code: str) -> tuple[object, object]:
        page = await site_app(site).state.flow_service.page(SUPER, "demo", code)
        saved = page.flow.working
        return (
            page.items,
            views.readout(saved.interaction_kind, saved.handling_action, nl_locale()),
        )

    first, second = await judged("een"), await judged("twee")

    assert first == second
    assert first[1] is not None
    assert "de deelnemer bellen" in first[1].action


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
    await create_ready(site, offer_contract="yes")
    await act(site, "publish")
    await act(site, "prepare")
    pages = [
        BASE,
        f"{BASE}/new",
        f"{BASE}/advies",
        f"{BASE}/advies/subjects/contract",
        f"{BASE}/advies/discard",
        f"{BASE}/advies/archive",
        f"{BASE}/advies/delete",
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


async def archive_directly(site: Site, pool_id: int) -> None:
    """Archive a pool behind the service's back: it refuses while a flow draws on the pool."""
    async with AsyncSession(site_app(site).state.engine) as session, session.begin():
        await session.execute(
            update(CapacityPoolRow).where(CapacityPoolRow.id == pool_id).values(archived_at=NOW)
        )


async def test_an_archived_pool_the_revision_uses_is_marked_and_kept_never_newly_chosen(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    old = await add_pool(site, "Oud")
    other = await add_pool(site, "Ander")
    await create(site, capacity_pool_id=str(old))
    await archive_directly(site, old)
    await archive_directly(site, other)

    editor = (await site.client.get(f"{BASE}/advies")).text
    kept = await save(site, capacity_pool_id=str(old))
    chosen = await save(site, capacity_pool_id=str(other))

    option = editor.split(f'<option value="{old}"')[1].split("</option>")[0]
    assert "selected" in option
    assert "Oud (gearchiveerd)" in option
    assert f'<option value="{other}"' not in editor  # an archived pool nobody uses is not offered
    assert kept.status_code == 303
    assert chosen.status_code == 422
    assert 'href="#capacity_pool_id"' in chosen.text
    assert "Deze keuze bestaat niet meer" in chosen.text


async def test_the_main_form_with_the_most_subjects_stays_far_below_the_size_limit(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    maximum = 100  # the highest ``max_subjects_per_group`` the superuser can set
    routing = GroupRouting(
        subjects=tuple(
            Subject.create(f"onderwerp-{n:03d}", {"nl": f"Onderwerp {n}", "en": f"Subject {n}"})
            for n in range(maximum)
        )
    )
    async with site_uow(site).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        await tx.save_group_routing(group.id, routing)
    skills = [await add_skill(site, f"vaardigheid-{n}", f"V{n}", f"S{n}") for n in range(10)]
    pool = await add_pool(site, "Advies", tuple(skills))
    await create(site, capacity_pool_id=str(pool))
    page = (await site.client.get(f"{BASE}/advies")).text
    form = page.split('id="flow-form-heading"')[1].split("</form>")[0]
    names = re.findall(r'<(?:input|select|textarea)[^>]*\bname="([^"]+)"', form)
    body = urlencode(
        {
            **{name: "yes" for name in names if name.startswith("offer_")},
            **{name: "on" for name in names if name.startswith("skill-")},
            "label_nl": "x" * 80,
            "label_en": "x" * 80,
            "flow_code": "advies",
        }
    )

    assert len([n for n in names if n.startswith("offer_")]) == maximum
    assert not [n for n in names if n.startswith("extra_")]
    assert len(body) < MAX_FORM_BYTES // 2, len(body)
    saved = await save(site, capacity_pool_id=str(pool), **dict(parse_qsl(body)))
    assert saved.status_code == 303


async def remove_subject(site: Site, code: str) -> None:
    """Remove the subject ``code`` from the service ``demo`` through the routing service."""
    await site_app(site).state.routing_service.remove_subject(SUPER, "demo", code)


async def test_a_subject_the_service_dropped_is_not_found_not_listed_and_dropped_on_save(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract", "aanvraag": "Aanvraag"})
    skill = await add_skill(site, "general", "Algemeen", "General")
    pool = await add_pool(site, "Advies", (skill,))
    await create(site, capacity_pool_id=str(pool), offer_contract="yes", offer_aanvraag="yes")
    await site.client.post(
        f"{BASE}/advies/subjects/contract", data={f"skill-{skill}": "on"}, headers=SAME_ORIGIN
    )
    await remove_subject(site, "contract")

    page_get = await site.client.get(f"{BASE}/advies/subjects/contract")
    page_post = await site.client.post(
        f"{BASE}/advies/subjects/contract", data={f"skill-{skill}": "on"}, headers=SAME_ORIGIN
    )
    editor = await site.client.get(f"{BASE}/advies")
    saved = await save(site, capacity_pool_id=str(pool), offer_aanvraag="yes")
    after = (await site.client.get(f"{BASE}/advies")).text

    assert page_get.status_code == page_post.status_code == 404
    assert editor.status_code == 200
    assert 'id="offer_contract"' not in editor.text  # not a row of the table
    assert 'id="offer_aanvraag"' in editor.text
    assert saved.status_code == 303
    assert f'href="{BASE}/advies/subjects/contract"' not in after
    async with site_uow(site).read() as tx:
        (stored,) = [f for f in await tx.list_service_flows(SUPER_SCOPE, 1) if f.code == "advies"]
    assert stored.draft is not None
    assert set(stored.draft.subject_skill_ids) == {"aanvraag"}


async def test_the_main_form_keeps_what_the_page_of_a_subject_saved_in_between(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    skill = await add_skill(site, "general", "Algemeen", "General")
    pool = await add_pool(site, "Advies", (skill,))
    await create(site, capacity_pool_id=str(pool), offer_contract="yes")
    rendered = await site.client.get(f"{BASE}/advies")  # the main form is open ...

    await site.client.post(  # ... while the subject page saves its skills ...
        f"{BASE}/advies/subjects/contract", data={f"skill-{skill}": "on"}, headers=SAME_ORIGIN
    )
    saved = await save(
        site, capacity_pool_id=str(pool), offer_contract="yes"
    )  # ... and then it posts

    assert rendered.status_code == 200
    assert saved.status_code == 303
    page = (await site.client.get(f"{BASE}/advies")).text
    assert "Algemeen" in page.split('id="subjects"')[1].split("</table>")[0]


async def test_an_error_of_the_subject_page_links_to_an_element_of_that_page(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    await create(site, offer_contract="yes")
    other = await add_skill(site, "theirs", "Hun", "Theirs", organisation="andere")

    response = await site.client.post(
        f"{BASE}/advies/subjects/contract", data={f"skill-{other}": "on"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    targets = re.findall(r'href="#([^"]+)"', response.text.split('role="alert"')[1])
    assert targets
    for target in targets:
        assert f'id="{target}"' in response.text


async def test_the_adjusted_notice_needs_a_chosen_channel(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    without_channel = await save(site, interaction_kind="", handling_action="call", label_en="")
    with_channel = await save(
        site, interaction_kind="telephone", handling_action="view_request", label_en=""
    )

    assert "De eerste actie is aangepast" not in without_channel.text
    assert "De eerste actie is aangepast" in with_channel.text


async def test_the_read_only_summary_says_intake_with_the_noun(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create_ready(site)
    await act(site, "publish")

    summary = (await site.client.get(f"{BASE}/advies")).text.split('<dl class="facts">')[1]

    assert "<dt>Aanmelding</dt>" in summary
    assert "Hoe komt een verzoek binnen?" not in summary
    english = (await site.client.get(f"{BASE}/advies", params={"lang": "en"})).text
    assert "<dt>Intake</dt>" in english.split('<dl class="facts">')[1]
