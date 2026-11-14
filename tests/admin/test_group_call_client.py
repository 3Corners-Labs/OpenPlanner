"""The call page chosen on the group settings form (ADR 0123 step 6a).

``embedded`` is a third value of ``join_client``. It is offered only while the platform switch
``embedded_call_enabled`` is on (a group that holds it keeps it on the list), and choosing it is
refused until the latest service-app check minted a guest token. The service's option for a way into
the video platform shows only for an embedded group. Group edit rights guard all of it, and the
audit event names the changed fields and no value.
"""

from datetime import UTC, datetime

from app.adapters.db.reports import ReportStore
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.diagnostics import CheckResult
from app.adapters.service_app_check import report_body
from app.domain.authorization import Scope
from app.domain.call_page import OtherWayOffer
from app.domain.guest_access import GUEST_PROBE
from app.domain.integration_check import SERVICE_APP_REPORT
from app.domain.scheduling import Group, JoinClient
from tests.admin.conftest import ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.support.group_settings import SETTINGS_FORM

GROUP = "/admin/groups/demo"
CLIENT = "join_client"
OPTION = "embedded_call_webex_option"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _app(site: Site):  # type: ignore[no-untyped-def]
    return site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001


def _switch(site: Site, *, on: bool) -> None:
    """Set the platform switch the way the portal's value would reach the running process."""
    app = _app(site)
    app.state.settings = app.state.settings.model_copy(update={"embedded_call_enabled": on})


async def _check(site: Site, *, guest: bool | None) -> None:
    """Store a service-app check: with the guest line passed or failed, or without one."""
    lines = [CheckResult("GET /meetings", 200, True, "")]
    if guest is not None:
        lines.append(CheckResult(GUEST_PROBE, 200 if guest else 403, guest, "scope"))
    await ReportStore(_app(site).state.engine).put(
        SERVICE_APP_REPORT, report_body(lines, wxcc_checked=False), NOW
    )


async def _post(site: Site, data: dict[str, str]) -> object:
    return await site.client.post(f"{GROUP}/settings", data=data, headers=SAME_ORIGIN)


async def _stored(site: Site) -> Group:
    async with SqlUnitOfWork(_app(site).state.engine).read() as tx:
        group = await tx.get_group("demo")
    assert group is not None
    return group


def _options(page: str, name: str = CLIENT) -> list[str]:
    select = page.split(f'name="{name}"', 1)[1].split("</select>", 1)[0]
    return [part.split('"', 1)[0] for part in select.split('<option value="')[1:]]


async def _embedded(site: Site, option: str | None = None) -> None:
    """Choose ``embedded`` the way an admin does, with the switch on and the check passed."""
    _switch(site, on=True)
    await _check(site, guest=True)
    await _post(site, {**SETTINGS_FORM, CLIENT: "embedded", **({OPTION: option} if option else {})})


# --- what the form offers ---------------------------------------------------------------------


async def test_the_form_offers_two_clients_while_the_switch_is_off(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get(f"{GROUP}/settings")).text

    assert _options(page) == ["browser", "choice"]
    assert f'name="{OPTION}"' not in page


async def test_the_form_offers_the_organisations_own_page_while_the_switch_is_on(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)

    page = (await site.client.get(f"{GROUP}/settings?lang=en")).text

    assert _options(page) == ["browser", "choice", "embedded"]
    assert "In the organisation&#39;s own page" in page or "In the organisation's own page" in page
    assert "Service App check on Integrations" in page  # the check has not run yet
    assert f'name="{OPTION}"' not in page  # the option acts only for an embedded group


# --- choosing it ------------------------------------------------------------------------------


async def test_embedded_is_refused_with_a_plain_sentence_until_the_guest_check_passed(
    site: Site,
) -> None:
    """Abuse case: a group saved as embedded without a passed check."""
    await site.login_as(SUPER)
    _switch(site, on=True)
    for guest in (None, False):  # never run with the switch on; run and refused by the platform
        await _check(site, guest=guest)

        response = await _post(site, {**SETTINGS_FORM, "name": "X", CLIENT: "embedded"})

        assert response.status_code == 422  # type: ignore[attr-defined]
        assert f'<a href="#{CLIENT}">' in response.text  # type: ignore[attr-defined]
        assert "voer eerst de controle" in response.text  # type: ignore[attr-defined]
        stored = await _stored(site)
        assert stored.join_client is JoinClient.BROWSER
        assert stored.name != "X"  # nothing at all was saved


async def test_embedded_submitted_while_the_switch_is_off_is_refused(site: Site) -> None:
    """Abuse case: a crafted post cannot choose a value the page does not offer."""
    await site.login_as(SUPER)
    await _check(site, guest=True)

    response = await _post(site, {**SETTINGS_FORM, CLIENT: "embedded"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert (await _stored(site)).join_client is JoinClient.BROWSER


async def test_embedded_is_saved_and_audited_once_the_check_minted_a_guest(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)
    await _check(site, guest=True)

    saved = await _post(site, {**SETTINGS_FORM, CLIENT: "embedded"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert (await _stored(site)).join_client is JoinClient.EMBEDDED
    page = (await site.client.get(f"{GROUP}/settings")).text
    assert '<option value="embedded" selected' in page
    assert f'name="{OPTION}"' in page  # now the option shows
    async with SqlUnitOfWork(_app(site).state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=5)
    detail = events[0].event.detail
    assert CLIENT in detail
    assert "embedded" not in detail  # the field names, never a value


async def test_a_nonsense_client_is_refused(site: Site) -> None:
    await site.login_as(SUPER)
    _switch(site, on=True)

    response = await _post(site, {**SETTINGS_FORM, CLIENT: "popup"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert f'<a href="#{CLIENT}">' in response.text  # type: ignore[attr-defined]


# --- holding it -------------------------------------------------------------------------------


async def test_a_held_embedded_choice_stays_on_the_list_and_says_what_the_switch_does(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    await _embedded(site)
    _switch(site, on=False)

    page = (await site.client.get(f"{GROUP}/settings?lang=en")).text

    assert _options(page) == ["browser", "choice", "embedded"]
    assert '<option value="embedded" selected' in page
    assert "participants of this group get the redirect to Webex for now" in page


async def test_saving_another_setting_never_resets_a_held_choice_or_asks_for_the_check(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    await _embedded(site)
    _switch(site, on=False)
    await _check(site, guest=False)  # the check has failed since; keeping it is no new choice

    saved = await _post(site, {**SETTINGS_FORM, "name": "Y", CLIENT: "embedded"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert (await _stored(site)).join_client is JoinClient.EMBEDDED


# --- the service's option ---------------------------------------------------------------------


async def test_the_option_is_saved_with_the_group_and_shown_back(site: Site) -> None:
    await site.login_as(SUPER)
    await _embedded(site)

    await _post(site, {**SETTINGS_FORM, CLIENT: "embedded", OPTION: "on_failure"})

    assert (await _stored(site)).embedded_call_webex_option is OtherWayOffer.ON_FAILURE
    page = (await site.client.get(f"{GROUP}/settings")).text
    assert _options(page, OPTION) == ["hidden", "on_failure", "always"]
    assert '<option value="on_failure" selected' in page


async def test_a_service_without_a_value_shows_its_organisations(site: Site) -> None:
    await site.login_as(SUPER)
    await _embedded(site)

    page = (await site.client.get(f"{GROUP}/settings")).text

    assert (await _stored(site)).embedded_call_webex_option is None
    assert '<option value="hidden" selected' in page  # the organisation's default


async def test_a_value_that_is_no_option_is_refused(site: Site) -> None:
    await site.login_as(SUPER)
    await _embedded(site)

    response = await _post(site, {**SETTINGS_FORM, CLIENT: "embedded", OPTION: "sometimes"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert f'<a href="#{OPTION}">' in response.text  # type: ignore[attr-defined]


async def test_a_form_without_the_option_keeps_what_the_service_has(site: Site) -> None:
    await site.login_as(SUPER)
    await _embedded(site, "always")

    await _post(site, {**SETTINGS_FORM, "name": "Z", CLIENT: "embedded"})  # an older page

    assert (await _stored(site)).embedded_call_webex_option is OtherWayOffer.ALWAYS


async def test_leaving_embedded_keeps_the_option_for_later(site: Site) -> None:
    await site.login_as(SUPER)
    await _embedded(site, "always")

    await _post(site, {**SETTINGS_FORM, CLIENT: "browser"})

    stored = await _stored(site)
    assert stored.join_client is JoinClient.BROWSER
    assert stored.embedded_call_webex_option is OtherWayOffer.ALWAYS


# --- who may --------------------------------------------------------------------------------


async def test_the_organisations_admin_may_change_the_choice_and_the_option(site: Site) -> None:
    """The option is a service's setting under ``group.edit``, not only the platform's."""
    await site.login_as(ORG_ADMIN)
    _switch(site, on=True)
    await _check(site, guest=True)

    saved = await _post(site, {**SETTINGS_FORM, CLIENT: "embedded"})
    again = await _post(site, {**SETTINGS_FORM, CLIENT: "embedded", OPTION: "always"})

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert again.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert (await _stored(site)).embedded_call_webex_option is OtherWayOffer.ALWAYS


async def test_a_manager_of_another_group_cannot_change_it(site: Site) -> None:
    """Abuse case: the choice is guarded by the group's own edit right."""
    await site.login_as(OUTSIDER)
    _switch(site, on=True)
    await _check(site, guest=True)

    response = await _post(site, {**SETTINGS_FORM, CLIENT: "embedded", OPTION: "always"})

    assert response.status_code in (302, 303, 403, 404)  # type: ignore[attr-defined]
    stored = await _stored(site)
    assert stored.join_client is JoinClient.BROWSER
    assert stored.embedded_call_webex_option is None
