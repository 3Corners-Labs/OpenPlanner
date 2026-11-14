"""The routing's task kind on the group settings form (ADR 0123 point 8, step 7a).

``form`` is the default. ``conversation`` is offered and accepted only while the platform switch
``embedded_call_enabled`` is on and the platform declares it carries conversations; the choice is
saved and audited with the routing's other attributes, under the same permission.
"""

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.contact_center import TaskKind
from tests.support.admin_site import OUTSIDER, SUPER, Site, post_form
from tests.support.group_settings import SETTINGS_FORM

GROUP = "/admin/groups/demo"
FIELD = "contact_center_task_kind"


def _switch(site: Site, *, on: bool) -> None:
    """Set the platform switch the way the portal's value would reach the running process."""
    app = site.app
    app.state.settings = app.state.settings.model_copy(update={"embedded_call_enabled": on})


async def _post(site: Site, data: dict[str, str]) -> object:
    return await post_form(site, f"{GROUP}/settings", data)


async def _stored(site: Site) -> TaskKind | None:
    app = site.app
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        group = await tx.get_group("demo")
    assert group is not None
    return group.contact_center.task_kind if group.contact_center else None


def _options(page: str) -> list[str]:
    select = page.split(f'name="{FIELD}"', 1)[1].split("</select>", 1)[0]
    return [part.split('"', 1)[0] for part in select.split('<option value="')[1:]]


async def test_the_form_offers_only_the_form_while_the_switch_is_off(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get(f"{GROUP}/settings")).text

    assert _options(page) == ["form"]


async def test_the_form_offers_the_conversation_while_the_switch_is_on(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)

    page = (await site.client.get(f"{GROUP}/settings")).text

    assert _options(page) == ["form", "conversation"]


async def test_a_conversation_submitted_while_the_switch_is_off_is_refused(site: Site) -> None:
    """Abuse case: a crafted post cannot choose a kind the page does not offer."""
    await site.login_as(SUPER)
    await _post(site, dict(SETTINGS_FORM))

    response = await _post(site, {**SETTINGS_FORM, "name": "X", FIELD: "conversation"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert f'<a href="#{FIELD}">' in response.text  # type: ignore[attr-defined]
    assert await _stored(site) is TaskKind.FORM  # nothing saved


async def test_no_note_while_the_switch_is_on_or_the_group_holds_the_form(site: Site) -> None:
    await site.login_as(SUPER)

    assert "maakt hij werkitems" not in (await site.client.get(f"{GROUP}/settings")).text
    _switch(site, on=True)
    await _post(site, {**SETTINGS_FORM, FIELD: "conversation"})
    assert "maakt hij werkitems" not in (await site.client.get(f"{GROUP}/settings")).text


async def test_a_kind_that_is_no_kind_is_refused(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)

    response = await _post(site, {**SETTINGS_FORM, FIELD: "chat"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert f'<a href="#{FIELD}">' in response.text  # type: ignore[attr-defined]


async def test_the_conversation_is_saved_audited_and_shown_while_the_switch_is_on(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)

    saved = await _post(site, {**SETTINGS_FORM, FIELD: "conversation"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert await _stored(site) is TaskKind.CONVERSATION
    page = (await site.client.get(f"{GROUP}/settings")).text
    assert '<option value="conversation" selected' in page
    app = site.app
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=5)
    assert "contact_center" in events[0].event.detail


async def test_a_conversation_already_held_survives_the_switch_going_off(site: Site) -> None:
    """Saving another setting never resets a held conversation unseen."""
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _post(site, {**SETTINGS_FORM, FIELD: "conversation"})
    _switch(site, on=False)

    page = (await site.client.get(f"{GROUP}/settings")).text
    assert _options(page) == ["form", "conversation"]
    assert '<option value="conversation" selected' in page
    assert "maakt hij werkitems" in page  # the note: in force only with the switch
    saved = await _post(site, {**SETTINGS_FORM, "name": "Y", FIELD: "conversation"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert await _stored(site) is TaskKind.CONVERSATION


async def test_a_form_without_the_select_keeps_the_kind(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _post(site, {**SETTINGS_FORM, FIELD: "conversation"})

    await _post(site, {**SETTINGS_FORM, "name": "Z"})  # an older page: no such input

    assert await _stored(site) is TaskKind.CONVERSATION


async def test_a_user_without_group_edit_cannot_change_it(site: Site) -> None:
    """The select is part of the settings form, so ``group.edit`` guards it as the rest."""
    await site.login_as(OUTSIDER)
    _switch(site, on=True)

    response = await _post(site, {**SETTINGS_FORM, FIELD: "conversation"})

    assert response.status_code in (302, 303, 403, 404)  # type: ignore[attr-defined]
    assert await _stored(site) is not TaskKind.CONVERSATION
