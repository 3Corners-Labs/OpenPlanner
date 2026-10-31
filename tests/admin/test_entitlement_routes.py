"""Entitlements over HTTP (ADR 0049): the login, the mapping page, the IdP values; A3, A9–A14."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.audit import Outcome
from app.domain.authorization import Role, Scope
from app.domain.entitlements import LoginClaims
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site, _running_site

SUPERUSER = LoginClaims("sub-root", "Root", entitlements=frozenset({"openplanner:superuser"}))
TEAM_LEAD = LoginClaims(
    "sub-lead", "Lea Lead", entitlements=frozenset({"openplanner:group_admin:demo/demo"})
)
MIXED = LoginClaims(
    "sub-mira",
    "Mira Mixed",
    entitlements=frozenset({"openplanner:org_admin:demo", "openplanner:auditor:andere"}),
)


@asynccontextmanager
async def mode_site(settings: Settings, admin_db: str, mode: str) -> AsyncIterator[Site]:
    """The app with ``OIDC_CLAIM_MODE`` set to ``mode``."""
    async with _running_site(
        settings.model_copy(update={"oidc_claim_mode": mode}), admin_db
    ) as running:
        yield running


@pytest.fixture
async def entitled(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    async with mode_site(settings, admin_db, "entitlements") as running:
        yield running


@pytest.fixture
async def shadow(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    async with mode_site(settings, admin_db, "shadow") as running:
        yield running


async def events(site: Site) -> list[tuple[str, str, Outcome, str, str | None, str]]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    async with uow.read() as tx:
        found = await tx.list_audit_events(Scope(everything=True), limit=100)
    return [
        (
            e.event.actor,
            e.event.action,
            e.event.outcome,
            e.event.reason,
            e.event.organisation,
            e.event.detail,
        )
        for e in reversed(found)
    ]


async def second_client(site: Site) -> httpx.AsyncClient:
    """Another browser on the same app, for a second session next to ``site.client``."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def login(client: httpx.AsyncClient, site: Site, claims: LoginClaims) -> httpx.Response:
    site.idp.next_claims = claims
    assert (await client.get("/admin/login")).status_code == 303
    return await client.get("/admin/callback", params={"code": "c", "state": "state-1"})


# --- the legacy safety net (point 14) -------------------------------------------------------


async def test_a3_an_ambiguous_legacy_login_is_refused_and_audited(site: Site) -> None:
    ambiguous = LoginClaims(
        "sub-bea",
        "Bea Beide",
        roles=frozenset({Role.ORG_ADMIN, Role.AUDITOR}),
        organisations=frozenset({"demo", "andere"}),
    )

    response = await site.login_with(ambiguous)

    assert response.status_code == 403
    assert "meer dan één rol" in response.text
    assert "Bea" not in response.text
    assert (await site.client.get("/admin/")).status_code == 303  # no session was started
    assert ("sub-bea", "auth.ambiguous_claims", Outcome.DENIED, "ambiguous_claims", None, "") in (
        await events(site)
    )


async def test_an_unambiguous_legacy_login_works_as_before(site: Site) -> None:
    two_roles_one_organisation = LoginClaims(
        "sub-two",
        "Tom Twee",
        roles=frozenset({Role.ORG_ADMIN, Role.AUDITOR}),
        organisations=frozenset({"demo"}),
    )

    assert (await site.login_with(two_roles_one_organisation)).status_code == 303
    assert (await site.client.get("/admin/groups/demo/settings")).status_code == 200


# --- entitlements mode (points 1, 9–11) ----------------------------------------------------


async def test_a1_an_entitlement_login_grants_exactly_its_values(entitled: Site) -> None:
    assert (await entitled.login_with(MIXED)).status_code == 303

    assert (await entitled.client.get("/admin/groups/demo/settings")).status_code == 200
    assert (await entitled.client.get("/admin/groups/andere-team/settings")).status_code == 403
    assert (await entitled.client.get("/admin/groups/andere-team")).status_code == 200
    rights = (await entitled.client.get("/admin/rights")).text
    assert "<code>org_admin:demo</code>" in rights
    assert "<code>auditor:andere</code>" in rights


async def test_a9_a12_access_follows_the_idp_value_and_a_change_takes_effect_at_once(
    entitled: Site,
) -> None:
    other = await second_client(entitled)
    async with other:
        assert (await login(other, entitled, TEAM_LEAD)).status_code == 303
        assert (await other.get("/admin/groups/demo")).status_code == 200

        await entitled.login_with(SUPERUSER)
        changed = await entitled.client.post(
            "/admin/groups/demo/settings/idp-key", data={"idp_key": "team-x"}, headers=SAME_ORIGIN
        )
        assert changed.status_code == 303
        assert changed.headers["location"] == "/admin/groups/demo/settings?done=saved"

        # A12: the open session holds the value, not the slug; the next request has no grant.
        assert (await other.get("/admin/groups/demo")).status_code == 403
        # A9: the old value (equal to the slug) grants nothing any more; the new one does.
        assert (await login(other, entitled, TEAM_LEAD)).status_code == 303
        assert (await other.get("/admin/groups/demo")).status_code == 403
        renamed = LoginClaims(
            "sub-lead", "Lea Lead", entitlements=frozenset({"openplanner:group_admin:demo/team-x"})
        )
        assert (await login(other, entitled, renamed)).status_code == 303
        assert (await other.get("/admin/groups/demo")).status_code == 200

    found = await events(entitled)
    assert (
        "sub-root",
        "group.idp_key_changed",
        Outcome.SUCCESS,
        "",
        "demo",
        "demo->team-x",
    ) in found
    assert (
        "sub-lead",
        "auth.entitlement_refused",
        Outcome.FAILURE,
        "",
        "demo",
        "openplanner:group_admin:demo/demo=unknown_group",
    ) in found


async def test_a10_the_idp_value_form_refuses_bad_values_and_other_organisations(
    entitled: Site,
) -> None:
    # Uniqueness within the organisation is `tests/domain/test_idp_mapping.py` (A10).
    await entitled.login_with(MIXED)

    invalid = await entitled.client.post(
        "/admin/groups/demo/settings/idp-key", data={"idp_key": "Demo Team"}, headers=SAME_ORIGIN
    )
    elsewhere = await entitled.client.post(
        "/admin/groups/andere-team/settings/idp-key", data={"idp_key": "x"}, headers=SAME_ORIGIN
    )

    assert invalid.status_code == 422
    assert 'href="#idp_key"' in invalid.text
    assert elsewhere.status_code == 403  # auditor in andere: not idp_mapping.manage


async def test_a_login_whose_values_are_all_refused_says_so_without_naming_them(
    entitled: Site,
) -> None:
    typo = LoginClaims("sub-rik", "Rik", entitlements=frozenset({"openplanner:org-admin:demo"}))

    assert (await entitled.login_with(typo)).status_code == 303
    page = await entitled.client.get("/admin/")

    assert page.status_code == 403
    assert "niet herkent" in page.text
    assert "org-admin" not in page.text


# --- shadow mode and what people see (points 8, 13; A13, A14) -----------------------------


async def test_a14_refusals_show_to_the_right_viewer_and_the_filter_finds_them(
    shadow: Site,
) -> None:
    rik = LoginClaims(
        "sub-rik",
        "Rik Refused",
        roles=frozenset({Role.ORG_ADMIN}),
        organisations=frozenset({"demo"}),
        entitlements=frozenset(
            {
                "openplanner:org_admin:demo",
                "openplanner:org_admin:gem-x",
                "openplanner:agent:demo/nope",
            }
        ),
    )
    assert (await shadow.login_with(rik)).status_code == 303
    own = (await shadow.client.get("/admin/rights")).text
    assert "openplanner:org_admin:gem-x" in own
    assert "openplanner:agent:demo/nope" in own

    await shadow.login_as(SUPER)
    listed = (await shadow.client.get("/admin/staff", params={"unrecognised": "1"})).text
    member_id = listed.split('">Rik Refused</a>')[0].rsplit('href="/admin/staff/', 1)[1]
    by_superuser = (await shadow.client.get(f"/admin/staff/{member_id}")).text
    await shadow.login_as(ORG_ADMIN)
    by_org_admin = (await shadow.client.get(f"/admin/staff/{member_id}")).text
    without_filter = (await shadow.client.get("/admin/staff", params={"unrecognised": "1"})).text

    assert "openplanner:org_admin:gem-x" in by_superuser
    assert "openplanner:agent:demo/nope" in by_superuser
    assert "onbekende organisatie" in by_superuser
    assert "openplanner:agent:demo/nope" in by_org_admin
    assert "gem-x" not in by_org_admin
    assert ">Rik Refused</a>" in without_filter
    assert ">Olaf Org</a>" not in without_filter


async def test_a13_shadow_mode_grants_the_legacy_claims_and_audits_the_mismatch(
    shadow: Site,
) -> None:
    richer = LoginClaims(
        "sub-rich",
        "Rich",
        roles=frozenset({Role.AUDITOR}),
        organisations=frozenset({"demo"}),
        entitlements=frozenset({"openplanner:org_admin:demo"}),
    )

    assert (await shadow.login_with(richer)).status_code == 303

    assert (await shadow.client.get("/admin/groups/demo/settings")).status_code == 403
    assert (await shadow.client.get("/admin/groups/demo")).status_code == 200
    assert (
        "sub-rich",
        "auth.entitlement_mismatch",
        Outcome.FAILURE,
        "",
        None,
        "legacy_only:auditor:demo;entitlements_only:org_admin:demo",
    ) in await events(shadow)


# --- the mapping page and the organisation form (points 4 and 17) --------------------------


async def test_the_mapping_page_lists_the_values_within_the_scope_and_exports_them(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    own = (await site.client.get("/admin/idp-mapping")).text
    await site.login_as(SUPER)
    everything = (await site.client.get("/admin/idp-mapping")).text
    export = await site.client.get("/admin/idp-mapping/export.txt")

    assert '<h1 id="page-title">Koppeling identiteitsprovider</h1>' in everything
    assert "<code>openplanner:superuser</code>" in everything
    assert "<code>openplanner:org_admin:demo</code>" in everything
    assert "<code>openplanner:group_admin:demo/demo</code>" in everything
    assert "<code>openplanner:org_admin:andere</code>" in everything
    assert "openplanner:superuser" not in own
    assert "openplanner:org_admin:andere" not in own
    assert "<code>openplanner:org_admin:demo</code>" in own
    assert 'href="/admin/idp-mapping"' in own  # the menu entry
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/plain")
    assert "openplanner:group_admin:demo/demo" in export.text.splitlines()
    assert ("sub-super", "idp_mapping.exported", Outcome.SUCCESS, "", None, "organisations:2") in (
        await events(site)
    )


async def test_the_superuser_sets_the_organisations_idp_value_on_its_page(site: Site) -> None:
    await site.login_as(SUPER)
    form = {"name": "Demo-gemeente", "default_locale": "nl"}

    page = (await site.client.get("/admin/organisations/demo")).text
    saved = await site.client.post(
        "/admin/organisations/demo", data={**form, "idp_key": "gem-demo"}, headers=SAME_ORIGIN
    )
    invalid = await site.client.post(
        "/admin/organisations/demo", data={**form, "idp_key": "GEM"}, headers=SAME_ORIGIN
    )
    taken = await site.client.post(
        "/admin/organisations/demo", data={**form, "idp_key": "andere"}, headers=SAME_ORIGIN
    )
    mapping = (await site.client.get("/admin/idp-mapping")).text

    assert 'name="idp_key"' in page
    assert saved.status_code == 303
    assert "done=saved" in saved.headers["location"]
    assert invalid.status_code == 422
    assert taken.status_code == 422
    assert "al in gebruik" in taken.text
    assert "<code>openplanner:org_admin:gem-demo</code>" in mapping
    assert (
        "sub-super",
        "organisation.idp_key_changed",
        Outcome.SUCCESS,
        "",
        "demo",
        "demo->gem-demo",
    ) in (await events(site))
