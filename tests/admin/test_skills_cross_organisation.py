"""Skills, pools and people of another organisation, by id, for every role (ADR 0121 step 2c).

The matrix table uses ids that exist nowhere; here the skill, the pool and the staff member
exist, in organisation ``andere``, and every role asks for them through both organisations'
addresses. Only the roles with the manage right in ``demo`` get as far as "no such thing" (404);
everyone else is refused by the matrix (403) before the id is looked at.
"""

import pytest

from tests.admin.conftest import SAME_ORIGIN, Site
from tests.support.authorization_matrix import ROLES
from tests.support.skill_seed import add_pool, add_skill, staff_id

DEMO, OTHER = "/admin/organisations/demo", "/admin/organisations/andere"
MANAGES_DEMO = {"superuser", "org_admin (demo)"}
MANAGES_ANDERE = {"superuser", "org_admin (andere)"}


@pytest.mark.matrix
@pytest.mark.parametrize("role", list(ROLES))
async def test_an_id_of_another_organisation_is_not_found_for_every_role(
    site: Site, role: str
) -> None:
    await site.login_as(ROLES["org_admin (andere)"])
    otto = await staff_id(site, ROLES["org_admin (andere)"].subject)
    skill = await add_skill(site, "theirs", organisation="andere")
    pool = await add_pool(site, "Theirs", organisation="andere")
    await site.login_as(ROLES[role])
    wanted = {"label_nl": "x", "label_en": "x", "pool_name": "x", "pool_code": "x"}

    async def status(method: str, path: str) -> int:
        response = await site.client.request(method, path, data=wanted, headers=SAME_ORIGIN)
        return response.status_code

    for suffix in ("skills/{s}", "skills/{s}/archive", "capacity/{p}", "capacity/{p}/archive"):
        path = suffix.format(s=skill, p=pool)
        for method in ("GET", "POST"):
            # Through demo's address: the id belongs to another organisation.
            expected = 404 if role in MANAGES_DEMO else 403
            assert await status(method, f"{DEMO}/{path}") == expected, (role, method, path)
            # Through its own organisation's address it exists, for whoever manages it.
            if role in MANAGES_ANDERE:
                assert await status(method, f"{OTHER}/{path}") in (200, 303), (role, method, path)
            else:
                assert await status(method, f"{OTHER}/{path}") == 403, (role, method, path)
    # The person of another organisation: unknown to demo's administrator, whichever address.
    for org, managers in (("demo", MANAGES_DEMO), ("andere", MANAGES_ANDERE)):
        response = await site.client.post(f"/admin/staff/{otto}/skills/{org}", headers=SAME_ORIGIN)
        if role not in managers:
            assert response.status_code == 403, (role, org)
        elif role == "org_admin (demo)":
            assert response.status_code == 404, (role, org)
        else:
            assert response.status_code == 303, (role, org)
