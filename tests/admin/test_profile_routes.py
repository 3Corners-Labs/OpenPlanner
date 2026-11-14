"""The own screen name on My profile (ADR 0123 step 5d)."""

from sqlalchemy import text

from app.domain.authorization import Role
from tests.admin.conftest import (
    AGENT,
    AUDITOR,
    GROUP_ADMIN,
    NO_ROLE,
    ORG_ADMIN,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.admin.test_staff_screen_name_routes import _events, _id_of, _login, _stored
from tests.support.principals import principal

PAGE = "/admin/rights/profile"
POST = "/admin/rights/profile/screen-name"
NAME = "Sanne"
TWO_ORGS = principal(
    "sub-two", "Tina Twee", frozenset({Role.ORG_ADMIN}), frozenset({"demo", "andere"}), frozenset()
)


async def test_the_page_shows_the_account_name_apart_from_the_screen_name_form(site: Site) -> None:
    await _login(site, GROUP_ADMIN)

    page = (await site.client.get(PAGE)).text

    assert '<h1 id="page-title">Mijn profiel</h1>' in page
    assert "Gina Group" in page
    assert "niet de naam die deelnemers zien" in page
    assert f'action="{POST}"' in page
    assert 'id="screen_name"' in page
    assert 'maxlength="40"' in page
    assert 'aria-current="page"' in page  # the sub-tab row of My rights
    assert 'href="/admin/rights/sessions"' in page
    assert "/admin/staff/" not in page.split(POST)[1].split("</form>")[0]  # no number anywhere
    assert 'href="/admin/rights/profile"' in page  # also in the top bar's profile menu


async def test_every_role_sets_and_clears_the_own_name_and_it_is_audited_without_it(
    site: Site,
) -> None:
    for person in (SUPER, ORG_ADMIN, GROUP_ADMIN, AUDITOR, AGENT):
        await _login(site, person)

        saved = await site.client.post(POST, data={"screen_name": f" {NAME} "}, headers=SAME_ORIGIN)

        assert saved.status_code == 303, person.subject
        assert saved.headers["location"] == f"{PAGE}?done=staff_screen_name_saved#screen-name"
        assert await _stored(site, person.subject) == NAME
        shown = (await site.client.get(saved.headers["location"])).text
        assert 'value="Sanne"' in shown
        assert "De schermnaam is opgeslagen." in shown

        same = await site.client.post(POST, data={"screen_name": NAME}, headers=SAME_ORIGIN)
        assert same.headers["location"].startswith(f"{PAGE}?done=staff_screen_name_unchanged")
        cleared = await site.client.post(POST, data={"screen_name": ""}, headers=SAME_ORIGIN)
        assert cleared.status_code == 303
        assert await _stored(site, person.subject) is None

    events = [e for e in await _events(site) if e[1] == "staff.screen_name_changed"]
    assert events
    for actor, _action, outcome, _reason, _organisation, detail in events:
        assert outcome == "success"
        assert detail == f"subject:{actor}"  # the actor is the subject: an own change
        assert NAME not in repr((actor, detail))
    assert {e[3] for e in events} == {"set", "cleared"}
    assert all(NAME not in repr(e) for e in events)


async def test_the_page_keeps_the_typed_text_and_ties_each_refusal_to_the_field(site: Site) -> None:
    await _login(site, GROUP_ADMIN)
    cases = (
        ('<script>alert("x")</script>', "alleen letters, cijfers"),
        ("G", "minstens 2 tekens"),
        ("x" * 41, "hoogstens 40 tekens"),
        ("..", "minstens één letter of cijfer"),
        ("́̂Sam", "accenten alleen op een letter"),
    )
    for typed, words in cases:
        refused = await site.client.post(POST, data={"screen_name": typed}, headers=SAME_ORIGIN)

        assert refused.status_code == 422, typed
        assert words in refused.text
        assert "<script>alert" not in refused.text
        assert 'id="screen_name-error"' in refused.text
        assert 'aria-describedby="screen_name-hint screen_name-error"' in refused.text
        assert 'aria-invalid="true"' in refused.text
    assert (
        "&lt;script&gt;"
        in (
            await site.client.post(POST, data={"screen_name": cases[0][0]}, headers=SAME_ORIGIN)
        ).text
    )
    assert await _stored(site, GROUP_ADMIN.subject) is None


async def test_the_form_never_reaches_another_persons_record(site: Site) -> None:
    """Abuse case: a staff member sets another's name through the own-profile form."""
    await _login(site, ORG_ADMIN, GROUP_ADMIN)
    forged = {
        "screen_name": NAME,
        "member_id": "1",
        "id": "1",
        "subject": ORG_ADMIN.subject,
        "staff_id": "1",
    }

    response = await site.client.post(
        f"{POST}?member_id=1&subject={ORG_ADMIN.subject}", data=forged, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert await _stored(site, GROUP_ADMIN.subject) == NAME
    assert await _stored(site, ORG_ADMIN.subject) is None
    for other in ("/admin/rights/profile/1/screen-name", "/admin/staff/1/profile"):
        assert (await site.client.post(other, data=forged, headers=SAME_ORIGIN)).status_code in (
            404,
            405,
        )
    assert await _stored(site, ORG_ADMIN.subject) is None


async def test_without_a_session_the_login_answers_and_nothing_is_written(site: Site) -> None:
    await _login(site, GROUP_ADMIN)
    site.client.cookies.clear()

    page = await site.client.get(PAGE, follow_redirects=False)
    posted = await site.client.post(
        POST, data={"screen_name": NAME}, headers=SAME_ORIGIN, follow_redirects=False
    )

    for answer in (page, posted):
        assert answer.status_code == 303
        assert answer.headers["location"].startswith("/admin/login")
    assert await _stored(site, GROUP_ADMIN.subject) is None


async def test_a_cross_site_post_is_refused(site: Site) -> None:
    await _login(site, GROUP_ADMIN)

    refused = await site.client.post(
        POST,
        data={"screen_name": NAME},
        headers={"origin": "https://elsewhere.example", "sec-fetch-site": "cross-site"},
    )

    assert refused.status_code == 403
    assert await _stored(site, GROUP_ADMIN.subject) is None


async def test_a_person_in_two_organisations_has_the_event_in_each_of_them(site: Site) -> None:
    await _login(site, TWO_ORGS)

    await site.client.post(POST, data={"screen_name": NAME}, headers=SAME_ORIGIN)

    where = sorted(
        str(e[4])
        for e in await _events(site)
        if e[1] == "staff.screen_name_changed" and e[0] == TWO_ORGS.subject
    )
    assert where == ["andere", "demo"]


async def test_a_login_without_a_role_has_no_page(site: Site) -> None:
    await _login(site, NO_ROLE)

    page = await site.client.get(PAGE, follow_redirects=False)
    posted = await site.client.post(POST, data={"screen_name": NAME}, headers=SAME_ORIGIN)

    assert page.status_code in (302, 303, 403)
    assert posted.status_code in (302, 303, 403)
    assert await _stored(site, NO_ROLE.subject) is None


async def test_an_erased_record_keeps_no_name_and_the_service_refuses_the_write(site: Site) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)
    await _login(site, SUPER)
    await site.client.post(f"/admin/staff/{gina}/erase", headers=SAME_ORIGIN)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    service = app.state.staff_names
    member = await service.own(GROUP_ADMIN.subject)
    result = await service.set_own_screen_name(GROUP_ADMIN, NAME)

    assert member is not None
    assert member.screen_name is None
    assert result.value == "erased"
    assert await _stored(site, GROUP_ADMIN.subject) is None


async def test_a_suspended_record_may_still_set_its_name(site: Site) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    result = await app.state.staff_names.set_own_screen_name(GROUP_ADMIN, NAME)

    assert result.value == "done"
    assert await _stored(site, GROUP_ADMIN.subject) == NAME


async def test_without_a_staff_record_the_page_says_so_and_the_post_is_a_conflict(
    site: Site,
) -> None:
    await _login(site, GROUP_ADMIN)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with app.state.engine.begin() as conn:
        await conn.execute(text("delete from staff where subject = :s"), {"s": GROUP_ADMIN.subject})

    page = await site.client.get(PAGE)
    posted = await site.client.post(POST, data={"screen_name": NAME}, headers=SAME_ORIGIN)

    assert page.status_code == 200
    assert "zodra uw login is vastgelegd" in page.text
    assert 'id="screen_name"' not in page.text
    assert posted.status_code == 409


async def test_the_page_and_the_menu_are_in_english_too(site: Site) -> None:
    await _login(site, GROUP_ADMIN)

    page = (await site.client.get(f"{PAGE}?lang=en")).text

    assert '<h1 id="page-title">My profile</h1>' in page
    assert "Account name" in page
    assert "Screen name for the participant" in page
    assert "not the full name" in page
    assert "Save screen name" in page


async def test_a_name_that_imitates_a_colleague_is_allowed_but_never_an_address_or_a_link(
    site: Site,
) -> None:
    """Abuse case: a name that imitates a colleague or a function. Uniqueness is not enforced."""
    await _login(site, ORG_ADMIN)
    await site.client.post(POST, data={"screen_name": NAME}, headers=SAME_ORIGIN)
    await _login(site, GROUP_ADMIN)

    same = await site.client.post(POST, data={"screen_name": NAME}, headers=SAME_ORIGIN)
    function = await site.client.post(POST, data={"screen_name": "Helpdesk"}, headers=SAME_ORIGIN)

    assert (same.status_code, function.status_code) == (303, 303)
    assert await _stored(site, ORG_ADMIN.subject) == NAME  # two people hold the same name
    for typed in ("sanne@voorbeeld.nl", "https://voorbeeld.nl", "Sanne: manager", "Sanne/Admin"):
        refused = await site.client.post(POST, data={"screen_name": typed}, headers=SAME_ORIGIN)
        assert refused.status_code == 422, typed
