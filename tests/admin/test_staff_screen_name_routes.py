"""The screen name of a staff member on their page (ADR 0123 step 5c)."""

from sqlalchemy import text

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.admin.conftest import (
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)

NAME = "Gina"


async def _id_of(site: Site, name: str) -> str:
    page = (await site.client.get("/admin/staff")).text
    return page.split(f'">{name}</a>')[0].rsplit('href="/admin/staff/', 1)[1]


async def _login(site: Site, *principals: object) -> None:
    """Let each person log in once (so they have a staff record); the last one stays."""
    for principal in principals:
        site.client.cookies.clear()
        await site.login_as(principal)  # type: ignore[arg-type]


async def _events(site: Site) -> list[tuple[str, str, str, str, str | None, str]]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        found = await tx.list_audit_events(Scope(everything=True), limit=300)
    return [
        (
            e.event.actor,
            e.event.action,
            e.event.outcome.value,
            e.event.reason,
            e.event.organisation,
            e.event.detail,
        )
        for e in found
    ]


async def _stored(site: Site, subject: str) -> str | None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with app.state.engine.connect() as conn:
        row = await conn.execute(
            text("select screen_name from staff where subject = :s"), {"s": subject}
        )
        return row.scalar_one()


async def test_a_manager_sets_the_name_and_it_is_shown_in_the_form_and_audited_without_it(
    site: Site,
) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")

    page = (await site.client.get(f"/admin/staff/{gina}")).text
    assert f'action="/admin/staff/{gina}/screen-name"' in page
    assert 'id="screen_name"' in page
    assert 'maxlength="40"' in page

    response = await site.client.post(
        f"/admin/staff/{gina}/screen-name", data={"screen_name": f"  {NAME} "}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/admin/staff/{gina}?done=staff_screen_name_saved#screen-name"
    )
    assert await _stored(site, GROUP_ADMIN.subject) == NAME
    shown = (await site.client.get(f"/admin/staff/{gina}?done=staff_screen_name_saved")).text
    assert 'value="Gina"' in shown
    assert "De schermnaam is opgeslagen." in shown
    events = await _events(site)
    assert (
        ORG_ADMIN.subject,
        "staff.screen_name_changed",
        "success",
        "set",
        "demo",
        f"subject:{GROUP_ADMIN.subject}",
    ) in events
    assert all(
        NAME not in repr(event) for event in events if event[1] == "staff.screen_name_changed"
    )


async def test_an_empty_text_clears_it_and_the_same_text_again_is_no_change(site: Site) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    url = f"/admin/staff/{gina}/screen-name"
    await site.client.post(url, data={"screen_name": NAME}, headers=SAME_ORIGIN)

    same = await site.client.post(url, data={"screen_name": NAME}, headers=SAME_ORIGIN)
    cleared = await site.client.post(url, data={"screen_name": ""}, headers=SAME_ORIGIN)

    assert same.headers["location"].startswith(
        f"/admin/staff/{gina}?done=staff_screen_name_unchanged"
    )
    assert cleared.headers["location"].startswith(
        f"/admin/staff/{gina}?done=staff_screen_name_saved"
    )
    assert await _stored(site, GROUP_ADMIN.subject) is None
    reasons = [e[3] for e in await _events(site) if e[1] == "staff.screen_name_changed"]
    assert reasons == ["cleared", "set"]  # newest first


async def test_a_refused_text_shows_the_page_again_with_the_reason_and_the_text_as_text(
    site: Site,
) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")

    response = await site.client.post(
        f"/admin/staff/{gina}/screen-name",
        data={"screen_name": '<script>alert("x")</script>'},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "alleen letters, cijfers" in response.text
    assert 'id="screen_name-error"' in response.text
    # The refusal is tied to the input through the shared field macro: described by hint and error.
    assert 'aria-describedby="screen_name-hint screen_name-error"' in response.text
    assert 'aria-invalid="true"' in response.text
    assert await _stored(site, GROUP_ADMIN.subject) is None
    short = await site.client.post(
        f"/admin/staff/{gina}/screen-name", data={"screen_name": "G"}, headers=SAME_ORIGIN
    )
    assert short.status_code == 422
    assert "minstens 2 tekens" in short.text
    for typed, words in (
        ("..", "minstens één letter of cijfer"),
        ("\u0301\u0302Sam", "accenten alleen op een letter"),
    ):
        refused = await site.client.post(
            f"/admin/staff/{gina}/screen-name", data={"screen_name": typed}, headers=SAME_ORIGIN
        )
        assert refused.status_code == 422
        assert words in refused.text


async def test_nobody_without_staff_manage_gets_the_form_or_may_post(site: Site) -> None:
    await _login(site, GROUP_ADMIN, AUDITOR)
    gina = await _id_of(site, "Gina Group")

    page = (await site.client.get(f"/admin/staff/{gina}")).text
    denied = await site.client.post(
        f"/admin/staff/{gina}/screen-name", data={"screen_name": NAME}, headers=SAME_ORIGIN
    )

    assert "/screen-name" not in page
    assert denied.status_code == 403
    assert await _stored(site, GROUP_ADMIN.subject) is None


async def test_another_organisations_manager_cannot_name_the_person(site: Site) -> None:
    """Abuse case: a manager of another organisation changes the name a participant sees."""
    await _login(site, GROUP_ADMIN, OUTSIDER)
    gina_id = await _login_and_id(site)

    denied = await site.client.post(
        f"/admin/staff/{gina_id}/screen-name", data={"screen_name": NAME}, headers=SAME_ORIGIN
    )

    assert denied.status_code == 403
    assert await _stored(site, GROUP_ADMIN.subject) is None
    assert (OUTSIDER.subject, "staff.manage", "denied", "out_of_scope", None, "") in await _events(
        site
    )


async def _login_and_id(site: Site) -> str:
    """The number of Gina's record, looked up as the superuser, who sees everyone."""
    await _login(site, SUPER)
    number = await _id_of(site, "Gina Group")
    await _login(site, OUTSIDER)
    return number


async def test_a_cross_site_post_is_refused(site: Site) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")

    refused = await site.client.post(
        f"/admin/staff/{gina}/screen-name",
        data={"screen_name": NAME},
        headers={"origin": "https://elsewhere.example", "sec-fetch-site": "cross-site"},
    )

    assert refused.status_code == 403
    assert await _stored(site, GROUP_ADMIN.subject) is None


async def test_a_login_does_not_lose_the_name(site: Site) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    await site.client.post(
        f"/admin/staff/{gina}/screen-name", data={"screen_name": NAME}, headers=SAME_ORIGIN
    )

    await _login(site, GROUP_ADMIN)

    assert await _stored(site, GROUP_ADMIN.subject) == NAME


async def test_erasing_the_person_clears_the_name_and_the_page_has_no_form(site: Site) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    await site.client.post(
        f"/admin/staff/{gina}/screen-name", data={"screen_name": NAME}, headers=SAME_ORIGIN
    )
    await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)
    await _login(site, SUPER)

    await site.client.post(f"/admin/staff/{gina}/erase", headers=SAME_ORIGIN)

    assert await _stored(site, GROUP_ADMIN.subject) is None
    page = (await site.client.get(f"/admin/staff/{gina}")).text
    assert "/screen-name" not in page
    refused = await site.client.post(
        f"/admin/staff/{gina}/screen-name", data={"screen_name": NAME}, headers=SAME_ORIGIN
    )
    assert refused.status_code == 409


async def test_the_list_marks_in_words_who_has_no_screen_name_while_the_organisation_shows_it(
    site: Site,
) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")

    before = (await site.client.get("/admin/staff")).text
    await site.client.post(
        f"/admin/staff/{gina}/screen-name", data={"screen_name": NAME}, headers=SAME_ORIGIN
    )
    after = (await site.client.get("/admin/staff")).text

    assert "Geen schermnaam" in before
    assert before.count("Geen schermnaam") == 2  # Gina and Olaf, who has none either
    assert after.count("Geen schermnaam") == 1


async def test_the_mark_is_gone_where_the_organisation_names_by_account_or_by_word(
    site: Site,
) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    for choice in ("term", "name"):
        saved = await site.client.post(
            "/admin/organisations/demo/settings/embedded_call_staff_name",
            data={"value": choice},
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 303
        await site.client._transport.app.state.live_settings.refresh()  # type: ignore[attr-defined]  # noqa: SLF001

        assert "Geen schermnaam" not in (await site.client.get("/admin/staff")).text


async def test_the_list_mark_and_the_page_are_in_english_too(site: Site) -> None:
    await _login(site, GROUP_ADMIN, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")

    listing = (await site.client.get("/admin/staff?lang=en")).text
    page = (await site.client.get(f"/admin/staff/{gina}?lang=en")).text

    assert "No screen name" in listing
    assert "Screen name for the participant" in page
    assert "not the full name" in page
