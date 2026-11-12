"""Flows of another service or organisation, by code, for every role (ADR 0121 step 2d, A05).

The matrix table uses codes that exist nowhere; here the flows exist, one in service ``demo`` and
one in ``andere-team`` (another organisation), and every role asks for each of them through both
services' addresses. Only the roles that may change the service in the path get as far as "no
such flow" (404) for the other side's code; everyone else is refused by the matrix (403) before
the code is looked at. A pool and a skill of the other organisation named in a form are an error
of the form, never a way in.
"""

import pytest

from app.domain.authorization import Scope
from tests.admin.conftest import SAME_ORIGIN, Site
from tests.support.admin_site import site_uow
from tests.support.authorization_matrix import ROLES
from tests.support.domain import make_group
from tests.support.flow_seed import add_flow, add_subjects
from tests.support.skill_seed import add_pool, add_skill

DEMO, OTHER = "/admin/groups/demo/flows", "/admin/groups/andere-team/flows"
MANAGES_DEMO = {"superuser", "org_admin (demo)"}
MANAGES_ANDERE = {"superuser", "org_admin (andere)"}
SUFFIXES = (
    "",
    "/prepare",
    "/publish",
    "/discard",
    "/activate",
    "/archive",
    "/subjects/x",
    "/delete",
)


@pytest.mark.matrix
@pytest.mark.parametrize("role", list(ROLES))
async def test_a_flow_code_of_another_service_is_not_found_for_every_role(
    site: Site, role: str
) -> None:
    await site.login_as(ROLES["org_admin (andere)"])
    their_pool = await add_pool(site, "Theirs", organisation="andere")
    await add_flow(site, "geheim", their_pool, group="andere-team", by=ROLES["org_admin (andere)"])
    mine = await add_pool(site, "Mine")
    await add_flow(site, "advies", mine)
    await site.login_as(ROLES[role])

    async def status(method: str, path: str) -> int:
        response = await site.client.request(
            method,
            path,
            data={"label_nl": "x", "label_en": "x", "flow_code": "x"},
            headers=SAME_ORIGIN,
        )
        return response.status_code

    for suffix in SUFFIXES:
        for method in ("GET", "POST"):
            if method == "GET" and suffix not in (
                "",
                "/discard",
                "/archive",
                "/subjects/x",
                "/delete",
            ):
                continue
            # Through demo's address: the code belongs to another organisation's service.
            expected = 404 if role in MANAGES_DEMO else 403
            assert await status(method, f"{DEMO}/geheim{suffix}") == expected, (
                role,
                method,
                suffix,
            )
            # Through its own service's address it exists, for whoever manages that service.
            if role in MANAGES_ANDERE:
                exists = (200, 303, 409, 422) + (
                    (404,) if "subjects" in suffix else ()
                )  # no such subject
                assert await status(method, f"{OTHER}/geheim{suffix}") in exists, (
                    role,
                    method,
                    suffix,
                )
            else:
                assert await status(method, f"{OTHER}/geheim{suffix}") == 403, (
                    role,
                    method,
                    suffix,
                )
            # And the other way round.
            expected = 404 if role in MANAGES_ANDERE else 403
            assert await status(method, f"{OTHER}/advies{suffix}") == expected, (
                role,
                method,
                suffix,
            )


async def test_a_pool_and_a_skill_of_another_organisation_are_form_errors_never_a_way_in(
    site: Site,
) -> None:
    await site.login_as(ROLES["org_admin (demo)"])
    await add_subjects(site, {"contract": "Contract"})
    their_pool = await add_pool(site, "Theirs", organisation="andere")
    their_skill = await add_skill(site, "theirs", "Hun", "Theirs", organisation="andere")
    base = {
        "flow_code": "advies",
        "label_nl": "x",
        "label_en": "x",
        "intake_appointment": "yes",
        "interaction_kind": "none",
        "dispatch": "own_queue",
        "completion": "operator",
    }

    for extra in (
        {"capacity_pool_id": str(their_pool)},
        {f"skill-{their_skill}": "on"},
    ):
        response = await site.client.post(DEMO, data={**base, **extra}, headers=SAME_ORIGIN)
        assert response.status_code == 422, extra
        assert "<code>advies</code>" not in (await site.client.get(DEMO)).text
    await add_flow(site, "advies", await add_pool(site, "Mine"), subjects=frozenset({"contract"}))
    response = await site.client.post(
        f"{DEMO}/advies/subjects/contract", data={f"skill-{their_skill}": "on"}, headers=SAME_ORIGIN
    )
    assert response.status_code == 422
    assert 'href="#required_skills"' in response.text


async def legacy_pools(site: Site) -> tuple[int, int]:
    """Return the legacy pool of ``demo`` and of a second service of the same organisation."""
    async with site_uow(site).locked() as tx:
        organisation = await tx.get_organisation("demo")
        assert organisation is not None
        second = await tx.add_group(
            make_group(id=0, organisation_id=organisation.id, slug="tweede", name="Tweede")
        )
        found = []
        for slug in ("demo", "tweede"):
            group = await tx.get_group(slug)
            assert group is not None
            await tx.ensure_default_flows(group.id)
            pools = await tx.list_capacity_pools(Scope(everything=True), organisation.id)
            found.append(next(p.id for p in pools if p.group_id == group.id))
    assert second.id
    return found[0], found[1]


async def test_a_legacy_pool_of_any_service_reads_as_a_pool_that_does_not_exist(site: Site) -> None:
    await site.login_as(ROLES["org_admin (demo)"])
    own, other = await legacy_pools(site)
    base = {
        "flow_code": "advies",
        "label_nl": "x",
        "label_en": "x",
        "intake_appointment": "yes",
        "interaction_kind": "none",
        "dispatch": "own_queue",
        "completion": "operator",
    }

    async def message(pool_id: int) -> str:
        response = await site.client.post(
            DEMO, data={**base, "capacity_pool_id": str(pool_id)}, headers=SAME_ORIGIN
        )
        assert response.status_code == 422, pool_id
        return response.text.split('id="capacity_pool_id-error"')[1].split("</p>")[0]

    unknown = await message(999_999)

    assert await message(own) == unknown
    assert await message(other) == unknown
    assert "<code>advies</code>" not in (await site.client.get(DEMO)).text


async def test_a_missing_and_a_foreign_skill_pool_and_flow_answer_alike(site: Site) -> None:
    await site.login_as(ROLES["org_admin (andere)"])
    their_pool = await add_pool(site, "Theirs", organisation="andere")
    their_skill = await add_skill(site, "theirs", "Hun", "Theirs", organisation="andere")
    await add_flow(site, "geheim", their_pool, group="andere-team", by=ROLES["org_admin (andere)"])
    await site.login_as(ROLES["org_admin (demo)"])
    org = "/admin/organisations/demo"
    pairs = [
        (f"{org}/skills/{their_skill}", f"{org}/skills/999999"),
        (f"{org}/capacity/{their_pool}", f"{org}/capacity/999999"),
        (f"{DEMO}/geheim", f"{DEMO}/bestaat-niet"),
    ]

    for foreign, missing in pairs:
        theirs, nothing = await site.client.get(foreign), await site.client.get(missing)
        assert theirs.status_code == nothing.status_code == 404, foreign
        assert theirs.text == nothing.text, foreign
