"""Subjects and routing screen (ADR 0081): subjects, routing questions, mapping, block binding."""

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.support.admin_site import GROUP_ADMIN, ORG_ADMIN, Site, post_form

PAGE = "/admin/groups/demo/settings/routing"
MOVE = {"code": "move", "label_nl": "Verhuizing", "label_en": "Moving"}


async def _details(site: Site) -> list[str]:
    app = site.app
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=50)
    return [e.event.detail for e in reversed(events) if e.event.action == "group.edit"]


async def test_the_settings_link_to_the_routing_screen(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    settings = (await site.client.get("/admin/groups/demo/settings")).text
    page = await site.client.get(PAGE)
    assert f'href="{PAGE}"' in settings
    assert "Deze dienst vraagt geen onderwerp" in settings
    assert page.status_code == 200
    assert "Nog geen onderwerpen" in page.text
    assert 'aria-current="page"' in page.text  # the Settings tab stays selected


async def test_a_subject_is_added_edited_and_removed(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    added = await post_form(site, f"{PAGE}/subjects", MOVE)
    assert added.status_code == 303
    page = (await site.client.get(PAGE)).text
    assert "<code>move</code>" in page
    edit = (await site.client.get(f"{PAGE}/subjects/move")).text
    assert 'value="Moving"' in edit
    saved = await post_form(
        site, f"{PAGE}/subjects/move", {"label_nl": "Verhuizen", "label_en": "Moving house"}
    )
    assert saved.status_code == 303
    assert "Verhuizen" in (await site.client.get(PAGE)).text
    removed = await post_form(site, f"{PAGE}/subjects/move/remove", {})
    assert removed.status_code == 303
    assert "<code>move</code>" not in (await site.client.get(PAGE)).text
    assert await _details(site) == [
        "subject.add,code:move",
        "subject.update,code:move",
        "subject.remove,code:move",
    ]


async def test_refusals_are_linked_to_their_inputs(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    bad_code = await post_form(site, f"{PAGE}/subjects", {**MOVE, "code": "Bad Code"})
    no_english = await post_form(site, f"{PAGE}/subjects", {**MOVE, "label_en": ""})
    assert {bad_code.status_code, no_english.status_code} == {422}
    assert '<a href="#code">Kies een code' in bad_code.text
    assert 'value="Verhuizing"' in bad_code.text  # what was typed stays
    assert '<a href="#label_en">Vul de naam in' in no_english.text


async def test_attributes_and_mapping_are_saved(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await post_form(site, f"{PAGE}/subjects", MOVE)
    switched = await post_form(site, f"{PAGE}/attributes", {"attribute_preferred_language": "yes"})
    assert switched.status_code == 303
    page = (await site.client.get(PAGE)).text
    assert (
        'id="attribute_preferred_language" name="attribute_preferred_language" value="yes" checked'
        in page
    )
    assert 'name="variable_preferred_language"' in page
    mapped = await post_form(
        site, f"{PAGE}/mapping/subject", {"variable_subject": "topic", "value_subject_move": "MV"}
    )
    assert mapped.status_code == 303
    page = (await site.client.get(PAGE)).text
    assert 'value="topic"' in page
    assert 'value="MV"' in page
    refused = await post_form(
        site, f"{PAGE}/mapping/subject", {"variable_subject": "openplannerGroup"}
    )
    assert refused.status_code == 422
    assert '<a href="#variable_subject">Gebruik letters' in refused.text
    bad_value = await post_form(
        site,
        f"{PAGE}/mapping/subject",
        {"variable_subject": "topic", "value_subject_move": "two words"},
    )
    assert bad_value.status_code == 422
    assert "Een waarde is een code" in bad_value.text
    assert await _details(site) == [
        "subject.add,code:move",
        "attributes.set,on:preferred_language",
        "mapping.set,source:subject",
    ]


async def test_a_block_offers_subjects_and_keeps_them_from_removal(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await post_form(site, f"{PAGE}/subjects", MOVE)
    staffing = (await site.client.get("/admin/groups/demo/staffing")).text
    assert 'name="subject_move"' in staffing
    added = await post_form(
        site,
        "/admin/groups/demo/staffing",
        {
            "weekday": "6",
            "opens": "09:00",
            "closes": "10:00",
            "capacity": "1",
            "offer": "scheduled",
            "subject_move": "yes",
        },
    )
    assert added.status_code == 303
    page = (await site.client.get(PAGE)).text
    assert "aan een blok gekoppeld" in page
    assert f"{PAGE}/subjects/move/remove" not in page
    refused = await post_form(site, f"{PAGE}/subjects/move/remove", {})
    assert refused.status_code == 422
    assert "Een blok biedt dit onderwerp nog aan" in refused.text


async def test_a_group_admin_cannot_open_the_routing_screen(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    assert (await site.client.get(PAGE)).status_code == 403
    assert (await post_form(site, f"{PAGE}/subjects", MOVE)).status_code == 403
