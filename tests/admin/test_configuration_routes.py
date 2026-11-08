"""The configuration document in the portal: export, plan, apply (ADR 0099 point 13)."""

import json
import re
from datetime import timedelta

from tests.admin.conftest import ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site

PLATFORM = "/admin/settings/configuration"
ORGANISATION = "/admin/organisations/demo/configuration"
PASSPHRASE = "a long enough passphrase"


async def export(site: Site, prefix: str = PLATFORM, **form: str) -> dict[str, object]:
    response = await site.client.post(f"{prefix}/export", data=form, headers=SAME_ORIGIN)
    assert response.status_code == 200, response.text
    return json.loads(response.content)  # type: ignore[no-any-return]


async def plan(site: Site, document: dict[str, object], prefix: str = PLATFORM, **form: str):
    return await site.client.post(
        f"{prefix}/plan",
        files={"document": ("config.json", json.dumps(document).encode(), "application/json")},
        data=form,
        headers=SAME_ORIGIN,
    )


def token_of(page: str) -> str:
    match = re.search(r'name="token" value="([^"]+)"', page)
    assert match, page
    return match.group(1)


async def test_an_exported_document_planned_again_changes_nothing(site: Site) -> None:
    await site.login_as(SUPER)
    document = await export(site)

    page = (await plan(site, document)).text

    assert "changes nothing" in page or "wijzigt niets" in page
    assert 'name="token"' not in page  # nothing to apply: no apply form


async def test_the_plan_lists_a_change_by_field_name_and_applying_it_writes_it(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    document = await export(site, PLATFORM)
    organisations = document["organisations"]
    assert isinstance(organisations, list)
    demo = next(o for o in organisations if o["slug"] == "demo")
    demo["name"] = "Hernoemd"

    planned = await plan(site, document)
    assert planned.status_code == 200
    assert "Hernoemd" not in planned.text  # field names, never values
    assert "<code>name" not in planned.text
    assert "name" in planned.text
    applied = await site.client.post(
        f"{PLATFORM}/apply", data={"token": token_of(planned.text)}, headers=SAME_ORIGIN
    )

    assert applied.status_code == 303
    assert applied.headers["location"].startswith("/admin/settings/package?done=")
    assert "Hernoemd" in (await site.client.get("/admin/organisations/demo")).text


async def test_an_apply_needs_the_token_of_the_plan_that_was_shown(site: Site) -> None:
    await site.login_as(SUPER)
    document = await export(site)
    await plan(site, document)

    wrong = await site.client.post(f"{PLATFORM}/apply", data={"token": "x"}, headers=SAME_ORIGIN)

    assert wrong.status_code == 409


async def test_the_next_upload_replaces_the_one_under_review(site: Site) -> None:
    await site.login_as(SUPER)
    document = await export(site)
    renamed = json.loads(json.dumps(document))
    renamed["organisations"][0]["name"] = "Eerst"
    first = token_of((await plan(site, renamed)).text)
    renamed["organisations"][0]["name"] = "Daarna"
    await plan(site, renamed)

    stale = await site.client.post(f"{PLATFORM}/apply", data={"token": first}, headers=SAME_ORIGIN)

    assert stale.status_code == 409


async def test_a_token_belongs_to_the_admin_who_uploaded_the_document(site: Site) -> None:
    await site.login_as(SUPER)
    document = await export(site)
    document["mode"] = "replace"
    planned = await plan(site, document)
    token = token_of(planned.text) if 'name="token"' in planned.text else "none"
    await site.login_as(ORG_ADMIN)

    other = await site.client.post(
        f"{ORGANISATION}/apply", data={"token": token}, headers=SAME_ORIGIN
    )

    assert other.status_code == 409


async def test_a_document_of_another_scope_is_refused(site: Site) -> None:
    await site.login_as(SUPER)
    organisation_document = await export(site, ORGANISATION)

    refused = await plan(site, organisation_document, PLATFORM)

    assert refused.status_code == 422
    assert 'name="token"' not in refused.text


async def test_an_organisation_admin_exports_and_plans_only_their_own_organisation(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    own = await export(site, ORGANISATION)
    assert [o["slug"] for o in own["organisations"]] == ["demo"]  # type: ignore[attr-defined]

    assert (await plan(site, own, ORGANISATION)).status_code == 200
    other = await site.client.post(
        "/admin/organisations/andere/configuration/export", data={}, headers=SAME_ORIGIN
    )
    assert other.status_code == 403
    assert (
        await site.client.post(f"{PLATFORM}/export", data={}, headers=SAME_ORIGIN)
    ).status_code == 403


async def test_an_outsider_cannot_open_the_page_of_another_organisation(site: Site) -> None:
    await site.login_as(OUTSIDER)

    assert (await site.client.get(ORGANISATION)).status_code == 403
    assert (await site.client.get("/admin/organisations/andere/configuration")).status_code == 200


async def test_secrets_need_a_repeated_passphrase_and_seal_under_it(site: Site) -> None:
    await site.login_as(SUPER)

    mismatch = await site.client.post(
        f"{PLATFORM}/export",
        data={"secrets": "yes", "passphrase": PASSPHRASE, "passphrase_repeat": "other"},
        headers=SAME_ORIGIN,
    )
    sealed = await export(site, secrets="yes", passphrase=PASSPHRASE, passphrase_repeat=PASSPHRASE)

    assert mismatch.status_code == 422
    assert "seal" in sealed


async def test_a_document_sealed_under_a_passphrase_plans_with_that_passphrase(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    sealed = await export(site, secrets="yes", passphrase=PASSPHRASE, passphrase_repeat=PASSPHRASE)

    right = await plan(site, sealed, passphrase=PASSPHRASE)

    assert right.status_code == 200


async def test_a_plan_that_deletes_carries_the_warning_and_a_danger_button(site: Site) -> None:
    await site.login_as(SUPER)
    document = await export(site, ORGANISATION)
    groups = document["organisations"][0]["groups"]  # type: ignore[index]
    extra = json.loads(json.dumps(groups[0]))
    extra["slug"] = "tijdelijk"
    extra["name"] = "Tijdelijk"
    created = await plan(
        site,
        {
            **document,
            "organisations": [{**document["organisations"][0], "groups": [*groups, extra]}],
        },
        ORGANISATION,
    )  # type: ignore[index, dict-item]
    assert created.status_code == 200, created.text
    await site.client.post(
        f"{ORGANISATION}/apply", data={"token": token_of(created.text)}, headers=SAME_ORIGIN
    )
    document["mode"] = "replace"

    planned = await plan(site, document, ORGANISATION)

    assert planned.status_code == 200
    assert "alert--warning" in planned.text
    assert "button--danger" in planned.text
    assert "tijdelijk" in planned.text
    assert 'name="token"' in planned.text


async def test_the_pages_work_without_javascript_and_name_their_forms(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/settings/package")).text
    org = (await site.client.get(ORGANISATION)).text

    for text in (page, org):
        assert 'action="' in text
        assert "/export" in text
        assert "/plan" in text
        assert "<script" not in text.split('id="document-heading"')[1].split("</section>")[0]


async def test_export_plan_and_apply_need_a_recent_login(site: Site) -> None:
    await site.login_as(SUPER)
    site.clock.advance(timedelta(minutes=30))
    await site.client.get("/admin/settings/package")
    site.clock.advance(timedelta(minutes=31))

    for prefix in (PLATFORM, ORGANISATION):
        for action in ("export", "plan", "apply"):
            response = await site.client.post(f"{prefix}/{action}", data={}, headers=SAME_ORIGIN)
            assert response.status_code == 303, (prefix, action)
    page = await site.client.get("/admin/settings/package")  # reading needs no fresh login
    assert page.status_code == 200


async def test_a_plan_page_is_not_cached_and_holds_no_value(site: Site) -> None:
    await site.login_as(SUPER)
    document = await export(site)
    document["organisations"][0]["name"] = "Geheimnaam"  # type: ignore[index]

    response = await plan(site, document)

    assert "Geheimnaam" not in response.text
    assert "no-store" in response.headers.get("cache-control", "")


async def test_an_organisation_admin_is_not_offered_the_secrets_they_may_not_export(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    org = (await site.client.get(ORGANISATION)).text
    await site.login_as(SUPER)
    org_as_superuser = (await site.client.get(ORGANISATION)).text

    assert 'name="secrets"' not in org
    assert 'name="passphrase_repeat"' not in org
    assert 'name="secrets"' in org_as_superuser


async def test_an_organisation_admin_cannot_ask_for_secrets_with_a_crafted_post(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        f"{ORGANISATION}/export",
        data={"secrets": "yes", "passphrase": PASSPHRASE, "passphrase_repeat": PASSPHRASE},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 403
    assert b"sealed" not in response.content
