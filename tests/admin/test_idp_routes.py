"""The *Login* sub-tab of an organisation: its identity providers (ADR 0083 step 2).

Registration, the write-only secret, the key pair and the JWKS, the guided test login that
starts no session (point 9), and what each role sees. B13 (an org admin registers) and B15
(the private key and the secret never reach a page) are among them.
"""

import json
import re

import pytest

from app.domain.audit import Outcome
from app.domain.entitlements import LoginClaims
from app.domain.identity_provider_service import TEST_LOGIN_EVENT
from app.domain.ports import LoginTestReport
from tests.admin.conftest import AUDITOR, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events
from tests.fakes.identity import FakeIdentityProvider
from tests.fakes.identity_providers import DISCOVERY_URL, discovery_document

LOGIN = "/admin/organisations/demo/login"
NEW_PROVIDER = {
    "key": "gem-a",
    "name": "Gemeente A",
    "discovery_url": DISCOVERY_URL,
    "client_id": "openplanner",
    "client_auth": "private_key_jwt",
    "scopes": "openid profile email",
    "entitlement_claim": "openplanner_entitlements",
    "entitlement_prefix": "openplanner",
}


async def register(site: Site, **overrides: str) -> str:
    """Register a provider through the form; return the page of the provider."""
    response = await site.client.post(
        LOGIN, data={**NEW_PROVIDER, **overrides}, headers=SAME_ORIGIN
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(f"{LOGIN}/{overrides.get('key', 'gem-a')}?done=idp_created")
    return location.split("?")[0]


@pytest.fixture
async def superuser(site: Site) -> Site:
    await site.login_as(SUPER)
    return site


async def test_the_login_sub_tab_is_offered_to_those_who_may_see_it(site: Site) -> None:
    for admin, shown in ((SUPER, True), (ORG_ADMIN, True), (AUDITOR, True), (OUTSIDER, False)):
        await site.login_as(admin)
        page = await site.client.get("/admin/organisations/demo")
        assert (page.status_code == 200) is shown or page.status_code == 403
        if shown:
            assert f'href="{LOGIN}"' in page.text


async def test_an_organisation_without_a_provider_says_so(superuser: Site) -> None:
    page = await superuser.client.get(LOGIN)

    assert page.status_code == 200
    assert "platformaccount" in page.text
    assert f'href="{LOGIN}/new"' in page.text


async def test_the_superuser_registers_a_provider_with_a_key_pair(superuser: Site) -> None:
    page_url = await register(superuser)

    page = await superuser.client.get(page_url)
    assert page.status_code == 200
    assert "kid-1" in page.text
    assert "/admin/jwks.json" in page.text
    assert "/admin/callback" in page.text
    assert "private-1" not in page.text  # B15
    assert ">Uit<" in page.text  # registered switched off
    events = await audit_events(superuser)
    assert ("sub-super", "idp.created", Outcome.SUCCESS, "") in events
    assert superuser.providers.invalidated == ["gem-a"]
    listing = await superuser.client.get(LOGIN)
    assert "Gemeente A" in listing.text


async def test_the_jwks_is_public_and_holds_the_public_keys_only(superuser: Site) -> None:
    await register(superuser)

    superuser.client.cookies.clear()  # nobody is logged in from here on
    anonymous = await superuser.client.get("/admin/jwks.json")
    assert anonymous.status_code == 200
    assert anonymous.headers["cache-control"] == "public, max-age=300"
    document = json.loads(anonymous.text)
    assert [k["kid"] for k in document["keys"]] == ["kid-1"]
    assert "d" not in document["keys"][0]
    assert "private" not in anonymous.text


async def test_a_client_secret_is_write_only(superuser: Site) -> None:
    page_url = await register(
        superuser, key="gem-s", client_auth="client_secret_basic", client_secret="top-secret"
    )

    page = await superuser.client.get(page_url)
    assert "top-secret" not in page.text
    assert "Opgeslagen (wordt niet getoond)" in page.text
    assert 'name="client_secret"' in page.text
    assert "kid-" not in page.text
    missing = await superuser.client.post(
        LOGIN,
        data={**NEW_PROVIDER, "key": "gem-t", "client_auth": "client_secret_basic"},
        headers=SAME_ORIGIN,
    )
    assert missing.status_code == 422
    assert 'id="client_secret-error"' in missing.text


async def test_form_errors_are_linked_to_their_inputs(superuser: Site) -> None:
    response = await superuser.client.post(
        LOGIN,
        data={
            **NEW_PROVIDER,
            "key": "Gem A",
            "discovery_url": "http://x/.well-known/openid-configuration",
        },
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert 'id="key-error"' in response.text
    assert "Gem A" not in re.sub(r'value="[^"]*"', "", response.text).split("key-error")[1][:200]


async def test_a_refused_discovery_check_explains_and_is_audited(superuser: Site) -> None:
    superuser.discovery.documents[DISCOVERY_URL] = discovery_document(
        code_challenge_methods_supported=["plain"]
    )

    response = await superuser.client.post(LOGIN, data=NEW_PROVIDER, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert 'id="discovery_url-error"' in response.text
    assert ("sub-super", "idp.created", Outcome.FAILURE, "no_pkce") in await audit_events(superuser)


async def test_changing_switching_rotating_and_removing(superuser: Site) -> None:
    page_url = await register(superuser)

    changed = await superuser.client.post(
        page_url, data={**NEW_PROVIDER, "name": "Gemeente A (nieuw)"}, headers=SAME_ORIGIN
    )
    assert changed.headers["location"] == f"{page_url}?done=saved"
    same = await superuser.client.post(
        page_url, data={**NEW_PROVIDER, "name": "Gemeente A (nieuw)"}, headers=SAME_ORIGIN
    )
    assert same.headers["location"] == f"{page_url}?done=unchanged"

    on = await superuser.client.post(
        f"{page_url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN
    )
    assert on.headers["location"] == f"{page_url}?done=idp_enabled"
    assert ">Aan<" in (await superuser.client.get(page_url)).text
    off = await superuser.client.post(f"{page_url}/enable", headers=SAME_ORIGIN)
    assert off.headers["location"] == f"{page_url}?done=idp_disabled"

    rotated = await superuser.client.post(f"{page_url}/rotate-key", headers=SAME_ORIGIN)
    assert rotated.headers["location"] == f"{page_url}?done=idp_key_rotated"
    page = (await superuser.client.get(page_url)).text
    assert "kid-2" in page
    assert "Ingetrokken sleutels" in page

    removed = await superuser.client.post(f"{page_url}/delete", headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{LOGIN}?done=removed"
    assert (await superuser.client.get(page_url)).status_code == 404
    actions = [e[1] for e in await audit_events(superuser)]
    for action in ("idp.updated", "idp.enabled", "idp.disabled", "idp.key_rotated", "idp.deleted"):
        assert action in actions


async def test_b13_the_organisation_admin_sees_the_status_but_changes_nothing(site: Site) -> None:
    await site.login_as(SUPER)
    page_url = await register(site)

    await site.login_as(ORG_ADMIN)
    listing = await site.client.get(LOGIN)
    assert listing.status_code == 200
    assert "Gemeente A" in listing.text
    assert f'href="{LOGIN}/new"' not in listing.text
    page = await site.client.get(page_url)
    assert page.status_code == 200
    assert "<form" not in page.text.split('id="page-title"')[1]
    assert (await site.client.get(f"{LOGIN}/new")).status_code == 403
    assert (
        await site.client.post(LOGIN, data=NEW_PROVIDER, headers=SAME_ORIGIN)
    ).status_code == 403
    assert (await site.client.post(f"{page_url}/delete", headers=SAME_ORIGIN)).status_code == 403
    await site.login_as(AUDITOR)
    assert (await site.client.get(page_url)).status_code == 200


# --- the guided test login (point 9) ---------------------------------------------------------


def organisation_claims(entitlements: frozenset[str] = frozenset()) -> LoginClaims:
    return LoginClaims(
        subject="idp:gem-a:ada",
        display_name="Ada",
        roles=frozenset(),
        organisations=frozenset(),
        groups=frozenset(),
        role_claims=frozenset(),
        entitlements=entitlements,
    )


async def start_test(site: Site) -> str:
    """Register a provider with a fake adapter and start the test login; return its page."""
    page_url = await register(site)
    adapter = FakeIdentityProvider()
    adapter.next_claims = organisation_claims(frozenset({"openplanner:org_admin:demo"}))
    site.providers.adapters["gem-a"] = adapter
    started = await site.client.post(f"{page_url}/test", headers=SAME_ORIGIN)
    assert started.status_code == 200
    assert "https://idp.test/authorize?x=1" in started.text
    assert adapter.fresh == [True]
    return page_url


async def test_a_test_login_shows_what_came_back_and_starts_no_session(superuser: Site) -> None:
    page_url = await start_test(superuser)

    result = await superuser.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert result.status_code == 200
    assert "Uitslag van de proeflogin" in result.text
    assert "example.test" in result.text
    assert "email_verified" in result.text
    assert "Organisatiebeheerder" in result.text or "org_admin" in result.text
    assert ("sub-super", TEST_LOGIN_EVENT, Outcome.SUCCESS, "") in await audit_events(superuser)
    still_super = await superuser.client.get(page_url)
    assert still_super.status_code == 200  # the superuser's own session is untouched
    assert "Sanne Super" in still_super.text


async def test_a_failed_test_login_is_explained_and_audited(superuser: Site) -> None:
    page_url = await start_test(superuser)
    superuser.providers.adapters["gem-a"].fail = "invalid_token"

    result = await superuser.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert result.status_code == 303
    assert result.headers["location"] == f"{page_url}?test_failed=invalid_token"
    page = await superuser.client.get(result.headers["location"])
    assert "ongeldig" in page.text
    assert ("sub-super", TEST_LOGIN_EVENT, Outcome.FAILURE, "invalid_token") in await audit_events(
        superuser
    )


async def test_a_test_login_reports_a_missing_or_unverified_email(superuser: Site) -> None:
    page_url = await start_test(superuser)
    adapter = superuser.providers.adapters["gem-a"]
    adapter.test_report = LoginTestReport(organisation_claims(), ("sub",), False, None)

    result = await superuser.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert result.status_code == 200
    assert result.text.count("Ontbreekt") == 1
    assert "Nee" in result.text
    assert page_url in result.text


async def test_b5_an_organisation_provider_completes_only_its_own_test_login(
    superuser: Site,
) -> None:
    """Without a pending test, nothing routes a login to an organisation provider yet."""
    page_url = await register(superuser)
    superuser.providers.adapters["gem-a"] = FakeIdentityProvider()
    await superuser.client.post(f"{page_url}/test", headers=SAME_ORIGIN)
    # A second start replaces the pending login; the stale answer must not complete anything.
    await superuser.client.get("/admin/login")
    superuser.idp.next_principal = SUPER

    answer = await superuser.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert answer.status_code == 303  # the platform login completed, not the test
    assert TEST_LOGIN_EVENT not in [e[1] for e in await audit_events(superuser)]


async def test_a_test_login_belongs_to_the_admin_who_started_it(site: Site) -> None:
    await site.login_as(SUPER)
    await start_test(site)
    # Another admin logging in on this browser does not inherit the pending test.
    await site.login_as(ORG_ADMIN)
    site.providers.adapters["gem-a"].next_claims = organisation_claims()

    with_other_session = await site.client.get(
        "/admin/callback", params={"code": "c", "state": "state-1"}
    )

    assert with_other_session.status_code == 400


async def test_the_mfa_policy_is_registered_shown_and_validated(superuser: Site) -> None:
    page_url = await register(superuser, mfa_policy="require", mfa_amr_values="otp  x-strong")

    page = await superuser.client.get(page_url)
    assert "Afdwingen" in page.text
    assert 'value="otp x-strong"' in page.text

    refused = await superuser.client.post(
        LOGIN,
        data={**NEW_PROVIDER, "key": "gem-b", "mfa_policy": "sometimes"},
        headers=SAME_ORIGIN,
    )
    assert refused.status_code == 422
    assert 'id="mfa_policy"' in refused.text


async def test_a_test_login_says_whether_a_second_factor_showed(superuser: Site) -> None:
    await start_test(superuser)

    result = await superuser.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert "Tweede factor getoond" in result.text
