"""The routing's task kind on the group settings form (ADR 0123 point 8, steps 7a and 6b).

``form`` is the default. ``conversation`` is offered and accepted only for a service that uses the
call page (its client is ``embedded`` and the platform switch is on: nothing else can show a chat
to the participant) and only when the platform declares it carries conversations; the choice is
saved and audited with the routing's other attributes, under the same permission. Since step 6b the
service's own client is the rule and the platform switch is the kill switch: it replaces the switch
as the thing that decides what the form offers.
"""

from app.adapters.db.reports import ReportStore
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.diagnostics import CheckResult
from app.adapters.service_app_check import report_body
from app.domain.authorization import Scope
from app.domain.contact_center import TaskKind
from app.domain.guest_access import GUEST_PROBE
from app.domain.integration_check import SERVICE_APP_REPORT
from app.domain.scheduling import JoinClient
from tests.support.admin_site import OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.support.call_page import set_group
from tests.support.domain import NOW
from tests.support.group_settings import SETTINGS_FORM

GROUP = "/admin/groups/demo"
FIELD = "contact_center_task_kind"
EMBEDDED_FORM = {**SETTINGS_FORM, "join_client": "embedded"}
"""The settings form of a service that uses the call page."""


def _switch(site: Site, *, on: bool) -> None:
    """Set the platform switch the way the portal's value would reach the running process."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    app.state.settings = app.state.settings.model_copy(update={"embedded_call_enabled": on})


async def _use_the_page(site: Site, client: JoinClient = JoinClient.EMBEDDED) -> None:
    """Make the demo service's client ``client`` (the switch is the test's own business)."""
    await set_group(site.client, "demo", join_client=client)


async def _post(site: Site, data: dict[str, str]) -> object:
    return await site.client.post(f"{GROUP}/settings", data=data, headers=SAME_ORIGIN)


async def _stored(site: Site) -> TaskKind | None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        group = await tx.get_group("demo")
    assert group is not None
    return group.contact_center.task_kind if group.contact_center else None


def _options(page: str) -> list[str]:
    select = page.split(f'name="{FIELD}"', 1)[1].split("</select>", 1)[0]
    return [part.split('"', 1)[0] for part in select.split('<option value="')[1:]]


async def test_the_form_offers_only_the_form_to_a_service_that_does_not_use_the_page(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)  # the switch alone offers nothing: the service's own client is the rule

    page = (await site.client.get(f"{GROUP}/settings")).text

    assert _options(page) == ["form"]


async def test_the_form_offers_the_conversation_to_a_service_that_uses_the_page(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _use_the_page(site)

    page = (await site.client.get(f"{GROUP}/settings")).text

    assert _options(page) == ["form", "conversation"]


async def test_the_switch_off_closes_the_offer_again_for_a_service_that_chose_the_page(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    await _use_the_page(site)  # a held choice, but the switch is the kill switch

    page = (await site.client.get(f"{GROUP}/settings")).text

    assert _options(page) == ["form"]


async def test_a_conversation_for_a_service_that_does_not_use_the_page_is_refused(
    site: Site,
) -> None:
    """Abuse case: a crafted post cannot choose a kind the page does not offer."""
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _post(site, dict(SETTINGS_FORM))

    response = await _post(site, {**SETTINGS_FORM, "name": "X", FIELD: "conversation"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert f'<a href="#{FIELD}">' in response.text  # type: ignore[attr-defined]
    assert await _stored(site) is TaskKind.FORM  # nothing saved


async def test_one_post_may_choose_the_page_and_the_conversation_together(site: Site) -> None:
    """The client the form chooses counts, so the two settings of one form agree with each other."""
    await site.login_as(SUPER)
    _switch(site, on=True)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await ReportStore(app.state.engine).put(
        SERVICE_APP_REPORT,
        report_body([CheckResult(GUEST_PROBE, 200, True, "")], wxcc_checked=False),
        NOW,
    )

    saved = await _post(site, {**EMBEDDED_FORM, FIELD: "conversation"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert await _stored(site) is TaskKind.CONVERSATION


async def test_choosing_the_conversation_and_leaving_the_page_in_one_post_is_refused(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _use_the_page(site)

    response = await _post(site, {**SETTINGS_FORM, "join_client": "browser", FIELD: "conversation"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert await _stored(site) is not TaskKind.CONVERSATION


async def test_no_note_while_the_service_uses_the_page_or_holds_the_form(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    assert "maakt hij werkitems" not in (await site.client.get(f"{GROUP}/settings")).text
    await _use_the_page(site)

    await _post(site, {**EMBEDDED_FORM, FIELD: "conversation"})

    assert "maakt hij werkitems" not in (await site.client.get(f"{GROUP}/settings")).text


async def test_a_kind_that_is_no_kind_is_refused(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _use_the_page(site)

    response = await _post(site, {**EMBEDDED_FORM, FIELD: "chat"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert f'<a href="#{FIELD}">' in response.text  # type: ignore[attr-defined]


async def test_the_conversation_is_saved_audited_and_shown_for_a_service_that_uses_the_page(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _use_the_page(site)

    saved = await _post(site, {**EMBEDDED_FORM, FIELD: "conversation"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert await _stored(site) is TaskKind.CONVERSATION
    page = (await site.client.get(f"{GROUP}/settings")).text
    assert '<option value="conversation" selected' in page
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=5)
    assert "contact_center" in events[0].event.detail


async def test_a_conversation_already_held_survives_the_service_leaving_the_page(
    site: Site,
) -> None:
    """Saving another setting never resets a held conversation unseen; the note says it."""
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _use_the_page(site)
    await _post(site, {**EMBEDDED_FORM, FIELD: "conversation"})
    await _use_the_page(site, JoinClient.BROWSER)  # the service no longer uses the page

    page = (await site.client.get(f"{GROUP}/settings")).text
    assert _options(page) == ["form", "conversation"]
    assert '<option value="conversation" selected' in page
    assert "maakt hij werkitems" in page  # the note: in force only for a service that uses it
    saved = await _post(site, {**SETTINGS_FORM, "name": "Y", FIELD: "conversation"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert await _stored(site) is TaskKind.CONVERSATION


async def test_a_form_without_the_select_keeps_the_kind(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _use_the_page(site)
    await _post(site, {**EMBEDDED_FORM, FIELD: "conversation"})

    await _post(site, {**EMBEDDED_FORM, "name": "Z"})  # an older page: no such input

    assert await _stored(site) is TaskKind.CONVERSATION


async def test_a_user_without_group_edit_cannot_change_it(site: Site) -> None:
    """The select is part of the settings form, so ``group.edit`` guards it as the rest."""
    await site.login_as(OUTSIDER)
    _switch(site, on=True)
    await _use_the_page(site)

    response = await _post(site, {**EMBEDDED_FORM, FIELD: "conversation"})

    assert response.status_code in (302, 303, 403, 404)  # type: ignore[attr-defined]
    assert await _stored(site) is not TaskKind.CONVERSATION


async def test_a_refused_form_shows_the_task_kind_for_the_client_that_was_posted(
    site: Site,
) -> None:
    """The two selects agree on a form that is shown again with errors (ADR 0123 step 6b)."""
    await site.login_as(SUPER)
    _switch(site, on=True)

    # Posted: the call page (offered, since the switch is on), refused until the guest check
    # passed. The task kind's select follows the posted client, not the stored one.
    toward = await _post(site, {**EMBEDDED_FORM, FIELD: "conversation"})
    assert toward.status_code == 422  # type: ignore[attr-defined]
    assert _options(toward.text) == ["form", "conversation"]  # type: ignore[attr-defined]
    assert 'value="embedded" selected' in toward.text  # type: ignore[attr-defined]

    # And the other way round: stored on the page, posted away from it, refused for its name.
    await _use_the_page(site)
    away = await _post(site, {**SETTINGS_FORM, "name": "", FIELD: "form"})
    assert away.status_code == 422  # type: ignore[attr-defined]
    assert _options(away.text) == ["form"]  # type: ignore[attr-defined]
    assert 'value="browser" selected' in away.text  # type: ignore[attr-defined]
