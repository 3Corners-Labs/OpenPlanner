"""Login domains on a provider's page (ADR 0083 points 10 to 12, ADR 0106 point 9).

B9 (claiming a domain the organisation does not own) is covered by the TXT verification; the
reserved suffix of the bundled provider is refused at the field.
"""

import pytest

from tests.support.admin_site import AUDITOR, ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.organisation_login import register


@pytest.fixture
async def provider_page(site: Site) -> str:
    await site.login_as(SUPER)
    return await register(site)


async def test_the_superuser_claims_and_verifies_a_domain(site: Site, provider_page: str) -> None:
    claimed = await site.client.post(
        f"{provider_page}/domains", data={"domain": "Acme-A.nl"}, headers=SAME_ORIGIN
    )
    page = (await site.client.get(provider_page)).text
    token = page.split("openplanner-domain-verification=", 1)[1].split("<", 1)[0]
    claim_id = page.split("/domains/", 1)[1].split("/", 1)[0]
    not_yet = await site.client.post(
        f"{provider_page}/domains/{claim_id}/verify", headers=SAME_ORIGIN
    )
    site.dns.records["_openplanner-challenge.acme-a.nl"] = (
        f"openplanner-domain-verification={token}",
    )
    verified = await site.client.post(
        f"{provider_page}/domains/{claim_id}/verify", headers=SAME_ORIGIN
    )

    assert claimed.status_code == 303
    assert "_openplanner-challenge.acme-a.nl" in page
    assert "Wacht op verificatie" in page
    assert not_yet.headers["location"].endswith("done=idp_domain_not_found_yet")
    assert verified.headers["location"].endswith("done=idp_domain_verified")
    assert "Geverifieerd" in (await site.client.get(provider_page)).text


async def test_the_reserved_suffix_is_refused_at_the_field(site: Site, provider_page: str) -> None:
    refused = await site.client.post(
        f"{provider_page}/domains",
        data={"domain": "demo.openplanner.internal"},
        headers=SAME_ORIGIN,
    )

    assert refused.status_code == 422
    assert "gereserveerd voor het platform" in refused.text
    assert 'aria-invalid="true"' in refused.text.split('id="domain"', 1)[1].split(">", 1)[0]


async def test_a_removed_domain_is_gone(site: Site, provider_page: str) -> None:
    await site.client.post(
        f"{provider_page}/domains", data={"domain": "acme-a.nl"}, headers=SAME_ORIGIN
    )
    page = (await site.client.get(provider_page)).text
    claim_id = page.split("/domains/", 1)[1].split("/", 1)[0]

    removed = await site.client.post(
        f"{provider_page}/domains/{claim_id}/remove", headers=SAME_ORIGIN
    )

    assert removed.status_code == 303
    assert "acme-a.nl" not in (await site.client.get(provider_page)).text


@pytest.mark.parametrize("who", [ORG_ADMIN, AUDITOR])
async def test_others_see_the_domains_but_cannot_change_them(
    site: Site, provider_page: str, who: object
) -> None:
    await site.client.post(
        f"{provider_page}/domains", data={"domain": "acme-a.nl"}, headers=SAME_ORIGIN
    )
    await site.login_as(who)  # type: ignore[arg-type]

    page = (await site.client.get(provider_page)).text
    refused = await site.client.post(
        f"{provider_page}/domains", data={"domain": "andere.nl"}, headers=SAME_ORIGIN
    )

    assert "acme-a.nl" in page
    assert f'action="{provider_page}/domains"' not in page
    assert refused.status_code == 403


async def test_the_suffix_setting_cannot_overlap_a_claimed_domain(
    site: Site, provider_page: str
) -> None:
    """ADR 0106 point 9: the platform suffix never lands on an organisation's domain."""
    await site.client.post(
        f"{provider_page}/domains", data={"domain": "example.nl"}, headers=SAME_ORIGIN
    )

    refused = await site.client.post(
        "/admin/settings/bundled_idp_login_suffix",
        data={"value": "staff.example.nl"},
        headers=SAME_ORIGIN,
    )
    allowed = await site.client.post(
        "/admin/settings/bundled_idp_login_suffix",
        data={"value": "medewerkers.internal"},
        headers=SAME_ORIGIN,
    )

    assert refused.status_code == 422
    assert "al geclaimd" in refused.text
    assert allowed.status_code == 303
