"""The *Login* sub-tab of organisation demo in the admin portal (ADR 0083 step 2)."""

from tests.support.admin_site import SAME_ORIGIN, SUPER, Site
from tests.support.organisation_providers import NEW_PROVIDER

LOGIN = "/admin/organisations/demo/login"
"""The organisation's list of identity providers, where a provider is registered."""


async def register(site: Site, **overrides: str) -> str:
    """Register provider ``org-a`` (values overridden) through the form; return its page."""
    response = await site.client.post(
        LOGIN, data={**NEW_PROVIDER, **overrides}, headers=SAME_ORIGIN
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(f"{LOGIN}/{overrides.get('key', 'org-a')}?done=idp_created")
    return location.split("?")[0]


async def route_domain(site: Site, domain: str = "acme-a.nl") -> str:
    """Register ``org-a``, switch it on and verify ``domain`` for it; return its page.

    Logs in as the superuser, then logs out again so the next request starts a login.
    """
    await site.login_as(SUPER)
    page_url = await register(site)
    await site.client.post(f"{page_url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN)
    await site.client.post(f"{page_url}/domains", data={"domain": domain}, headers=SAME_ORIGIN)
    page = (await site.client.get(page_url)).text
    token = page.split("openplanner-domain-verification=", 1)[1].split("<", 1)[0]
    claim_id = page.split("/domains/", 1)[1].split("/", 1)[0]
    site.dns.records[f"_openplanner-challenge.{domain}"] = (
        f"openplanner-domain-verification={token}",
    )
    verified = await site.client.post(f"{page_url}/domains/{claim_id}/verify", headers=SAME_ORIGIN)
    assert verified.headers["location"].endswith("idp_domain_verified")
    site.client.cookies.clear()
    return page_url
