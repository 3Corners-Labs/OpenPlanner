"""The *Login* sub-tab of organisation demo in the admin portal (ADR 0083 step 2)."""

from tests.admin.conftest import SAME_ORIGIN, Site
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
