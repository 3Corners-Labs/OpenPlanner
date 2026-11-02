"""Logins routed to an organisation provider by e-mail domain (ADR 0083 step 3c).

The e-mail-first page, ``login_hint``, the reserved suffix of the bundled provider (ADR 0106
point 9), and the abuse cases B1, B2 (rights outside the organisation), B3, B4 (the address
must be verified and on the provider's own domain), B5 (RFC 9207), B12 (the address is not
stored) and B14 (a switched-off provider ends its sessions).
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import Settings
from app.domain.entitlements import LoginClaims
from tests.admin.conftest import SAME_ORIGIN, SUPER, Site, _running_site
from tests.admin.test_idp_routes import register
from tests.fakes.identity import FakeIdentityProvider

P = "openplanner"
ADDRESS = "jan@acme-a.nl"


async def _route_domain(site: Site) -> str:
    """Register org-a, switch it on and verify acme-a.nl for it; return its page."""
    await site.login_as(SUPER)
    page_url = await register(site)
    await site.client.post(f"{page_url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN)
    await site.client.post(
        f"{page_url}/domains", data={"domain": "acme-a.nl"}, headers=SAME_ORIGIN
    )
    page = (await site.client.get(page_url)).text
    token = page.split("openplanner-domain-verification=", 1)[1].split("<", 1)[0]
    claim_id = page.split("/domains/", 1)[1].split("/", 1)[0]
    site.dns.records["_openplanner-challenge.acme-a.nl"] = (
        f"openplanner-domain-verification={token}",
    )
    verified = await site.client.post(f"{page_url}/domains/{claim_id}/verify", headers=SAME_ORIGIN)
    assert verified.headers["location"].endswith("idp_domain_verified")
    site.client.cookies.clear()
    return page_url


def _organisation_adapter(site: Site, email: str = ADDRESS) -> FakeIdentityProvider:
    adapter = FakeIdentityProvider()
    adapter.next_claims = LoginClaims(
        subject="idp:org-a:1234",
        display_name="Jan",
        entitlements=frozenset({f"{P}:org_admin:demo", f"{P}:org_admin:andere", f"{P}:superuser"}),
    )
    adapter.email = email
    site.providers.adapters["org-a"] = adapter
    return adapter


@pytest.fixture
async def routed(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    switched_on = settings.model_copy(update={"organisation_idps_enabled": True})
    async with _running_site(switched_on, admin_db) as running:
        running.page_url = await _route_domain(running)  # type: ignore[attr-defined]
        yield running


async def _log_in(site: Site, identifier: str) -> tuple[int, str]:
    start = await site.client.post(
        "/admin/login", data={"identifier": identifier}, headers=SAME_ORIGIN
    )
    assert start.status_code == 303, start.text[:300]
    done = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})
    return done.status_code, done.text


async def test_the_login_page_asks_for_the_address_first(routed: Site) -> None:
    page = await routed.client.get("/admin/login")

    assert page.status_code == 200
    assert "Werk-e-mailadres of inlognaam" in page.text
    assert 'autocomplete="username"' in page.text
    assert "/admin/login?platform=1" in page.text


async def test_an_address_on_a_routed_domain_logs_in_at_the_organisation_only(
    routed: Site,
) -> None:
    """B1 and B2: only the provider's own organisation; superuser and others are refused."""
    adapter = _organisation_adapter(routed)

    status, _ = await _log_in(routed, ADDRESS)
    own = await routed.client.get("/admin/organisations/demo")
    other = await routed.client.get("/admin/organisations/andere")
    platform = await routed.client.get("/admin/settings")

    assert status == 303
    assert adapter.login_hints == [ADDRESS]
    assert [h for h in routed.idp.login_hints if h] == []
    assert own.status_code == 200
    assert other.status_code in (403, 404)
    assert platform.status_code == 403


async def test_the_address_must_be_verified_and_on_the_provider_s_domain(routed: Site) -> None:
    """B3 and B4."""
    adapter = _organisation_adapter(routed, email="jan@elders.nl")
    mismatch, text = await _log_in(routed, ADDRESS)
    adapter.email, adapter.email_verified = ADDRESS, None
    unverified, _ = await _log_in(routed, ADDRESS)

    assert mismatch == 401
    assert "hoort niet bij een domein" in text
    assert unverified == 401


async def test_a_response_from_another_issuer_is_refused(routed: Site) -> None:
    """B5: the fake adapter receives the ``iss`` the provider sent back (RFC 9207)."""
    adapter = _organisation_adapter(routed)
    await routed.client.post("/admin/login", data={"identifier": ADDRESS}, headers=SAME_ORIGIN)

    await routed.client.get(
        "/admin/callback", params={"code": "c", "state": "state-1", "iss": "https://evil.test"}
    )

    assert adapter.issuers == ["https://evil.test"]


async def test_the_platform_suffix_and_unknown_domains_go_to_the_platform(routed: Site) -> None:
    adapter = _organisation_adapter(routed)

    for identifier in ("jan.jansen@demo.openplanner.internal", "jan@elders.nl", "super"):
        start = await routed.client.post(
            "/admin/login", data={"identifier": identifier}, headers=SAME_ORIGIN
        )
        assert start.status_code == 303

    assert [h for h in routed.idp.login_hints if h] == [
        "jan.jansen@demo.openplanner.internal",
        "jan@elders.nl",
        "super",
    ]
    assert adapter.login_hints == []


async def test_an_empty_identifier_is_explained(routed: Site) -> None:
    refused = await routed.client.post(
        "/admin/login", data={"identifier": "  "}, headers=SAME_ORIGIN
    )

    assert refused.status_code == 422
    assert 'aria-invalid="true"' in refused.text


async def test_a_switched_off_provider_ends_its_sessions(routed: Site) -> None:
    """B14: the next request finds the session's provider off and is logged out."""
    _organisation_adapter(routed)
    await _log_in(routed, ADDRESS)
    assert (await routed.client.get("/admin/organisations/demo")).status_code == 200
    staff = httpx.Cookies(routed.client.cookies)
    routed.client.cookies.clear()
    await routed.login_as(SUPER)
    page_url = routed.page_url  # type: ignore[attr-defined]
    await routed.client.post(f"{page_url}/enable", data={}, headers=SAME_ORIGIN)
    routed.client.cookies.clear()
    routed.client.cookies.update(staff)

    after = await routed.client.get("/admin/organisations/demo")

    assert after.status_code in (303, 401)


async def test_without_the_host_switch_the_login_goes_straight_to_the_platform(
    site: Site,
) -> None:
    await _route_domain(site)

    start = await site.client.get("/admin/login")

    assert start.status_code == 303


async def test_a_fresh_login_and_the_logout_go_to_the_session_s_own_provider(
    routed: Site,
) -> None:
    """ADR 0083 point 17: re-authentication never asks the address again nor switches."""
    adapter = _organisation_adapter(routed)
    await _log_in(routed, ADDRESS)

    fresh = await routed.client.get("/admin/login", params={"next": "/admin/"})
    logout = await routed.client.post("/admin/logout", headers=SAME_ORIGIN)

    assert fresh.status_code == 303
    assert adapter.login_hints == [ADDRESS, None]
    assert "https://idp.test/logout" in logout.text
    assert (adapter.logouts, routed.idp.logouts) == (1, 0)
    assert [h for h in routed.idp.login_hints if h] == []


async def test_the_pages_say_when_the_host_switch_is_off(site: Site) -> None:
    await _route_domain(site)
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/organisations/demo/login")).text

    assert "staan op deze host uit" in page


async def test_the_staff_directory_names_and_filters_the_login_service(routed: Site) -> None:
    """ADR 0083 point 16: the provider of each person, as a column and a filter."""
    _organisation_adapter(routed)
    await _log_in(routed, ADDRESS)
    routed.client.cookies.clear()
    await routed.login_as(SUPER)

    everyone = (await routed.client.get("/admin/staff")).text
    only = (await routed.client.get("/admin/staff", params={"provider": "org-a"})).text
    platform = (await routed.client.get("/admin/staff", params={"provider": "platform"})).text

    assert "Inlogdienst" in everyone
    assert 'name="provider"' in everyone
    assert ">org-a<" in only
    assert ">Platform<" not in only.split("<tbody>", 1)[-1].split("</table>", 1)[0]
    assert ">org-a<" not in platform.split("<tbody>", 1)[-1].split("</table>", 1)[0]


async def test_system_status_lists_the_organisation_providers(routed: Site) -> None:
    await routed.login_as(SUPER)

    page = (await routed.client.get("/admin/settings")).text

    assert "Identity providers van organisaties" in page
    assert "Aan op deze host" in page
    assert "<code>org-a</code>" in page
    assert "1 geverifieerd" in page
