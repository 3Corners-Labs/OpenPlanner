"""The superuser's API pages (ADR 0023): switches, register, show the secret once, manage."""

import re
from datetime import UTC, datetime

import pytest

from app.admin.api_routes import narrow_clients
from app.domain.api_access import ApiAccessService
from app.domain.api_clients import ApiClient, credential_hash
from tests.support.admin_site import ORG_ADMIN, SAME_ORIGIN, SUPER, Site

NEW_CLIENT = {
    "name": "WxCC AI Agent",
    "organisation": "demo",
    "lifetime_days": "90",
    "scope.availability:read": "on",
    "group.demo": "on",
    "networks": "198.51.100.0/24",
}


async def register(site: Site) -> tuple[str, str]:
    """Create a client through the form; return its id and the secret from the page."""
    response = await site.client.post("/admin/api/clients", data=NEW_CLIENT, headers=SAME_ORIGIN)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    client_id = re.search(r"opc_[A-Za-z0-9_-]+", response.text)
    secret = re.search(r"ops_[A-Za-z0-9_-]+", response.text)
    assert client_id is not None
    assert secret is not None
    return client_id.group(0), secret.group(0)


def service(site: Site) -> ApiAccessService:
    app = site.app
    found: ApiAccessService = app.state.api_access
    return found


@pytest.fixture
async def superuser(site: Site) -> Site:
    await site.login_as(SUPER)
    return site


async def test_navigation_shows_api_clients_to_the_superuser_only(site: Site) -> None:
    await site.login_as(SUPER)
    assert 'href="/admin/api"' in (await site.client.get("/admin/")).text
    await site.login_as(ORG_ADMIN)
    assert 'href="/admin/api"' not in (await site.client.get("/admin/")).text


async def test_overview_explains_the_environment_switch(superuser: Site) -> None:
    page = await superuser.client.get("/admin/api")

    assert page.status_code == 200
    assert "AGENT_API_ENABLED" in page.text
    assert "/api/agent/v1/docs" in page.text


async def test_switches_are_saved_and_audited(superuser: Site) -> None:
    response = await superuser.client.post(
        "/admin/api/settings", data={"enabled": "on", "docs_public": "on"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/api?done=saved"
    settings = await service(superuser).settings()
    assert (settings.enabled, settings.docs_public) == (True, True)
    audit = (await superuser.client.get("/admin/audit")).text
    assert "Documentatie publiceren" in audit or "Publish documentation" in audit


async def test_register_shows_the_secret_once_and_stores_only_its_hash(superuser: Site) -> None:
    client_id, secret = await register(superuser)

    stored = await service(superuser).client(SUPER, client_id)
    assert stored.secret_hash == credential_hash(secret)
    assert stored.groups == {"demo"}
    assert stored.networks == ("198.51.100.0/24",)
    detail = await superuser.client.get(f"/admin/api/clients/{client_id}")
    overview = await superuser.client.get("/admin/api")
    for page in (detail, overview):
        assert page.status_code == 200
        assert client_id in page.text
        assert secret not in page.text


async def test_invalid_form_is_answered_with_linked_errors(superuser: Site) -> None:
    bad = {**NEW_CLIENT, "networks": "not-a-network"}
    del bad["scope.availability:read"]

    response = await superuser.client.post("/admin/api/clients", data=bad, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert 'href="#scopes"' in response.text
    assert "ops_" not in response.text


async def test_groups_of_another_organisation_are_refused(superuser: Site) -> None:
    data = {**NEW_CLIENT, "group.andere-team": "on"}

    response = await superuser.client.post("/admin/api/clients", data=data, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert 'href="#groups"' in response.text


async def test_update_rotate_disable_and_revoke(superuser: Site) -> None:
    client_id, secret = await register(superuser)
    url = f"/admin/api/clients/{client_id}"

    renamed = await superuser.client.post(
        url, data={**NEW_CLIENT, "name": "Bot Klantenservice"}, headers=SAME_ORIGIN
    )
    assert renamed.headers["location"] == f"{url}?done=saved"
    assert (await service(superuser).client(SUPER, client_id)).name == "Bot Klantenservice"

    rotated = await superuser.client.post(
        f"{url}/rotate", data={"lifetime_days": "365"}, headers=SAME_ORIGIN
    )
    new_secret = re.search(r"ops_[A-Za-z0-9_-]+", rotated.text)
    assert rotated.status_code == 200
    assert new_secret is not None
    assert new_secret.group(0) != secret

    off = await superuser.client.post(f"{url}/enable", data={}, headers=SAME_ORIGIN)
    assert off.headers["location"] == f"{url}?done=client_disabled"
    assert not (await service(superuser).client(SUPER, client_id)).enabled
    on = await superuser.client.post(f"{url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN)
    assert on.headers["location"] == f"{url}?done=client_enabled"

    revoked = await superuser.client.post(f"{url}/revoke", headers=SAME_ORIGIN)
    assert revoked.headers["location"] == f"{url}?done=tokens_revoked"


async def test_rotating_with_an_unknown_lifetime_is_refused(superuser: Site) -> None:
    client_id, _ = await register(superuser)

    response = await superuser.client.post(
        f"/admin/api/clients/{client_id}/rotate",
        data={"lifetime_days": "9999"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert "ops_" not in response.text


async def test_cross_site_posts_are_refused(superuser: Site) -> None:
    response = await superuser.client.post(
        "/admin/api/settings", data={"enabled": "on"}, headers={"sec-fetch-site": "cross-site"}
    )

    assert response.status_code == 403
    assert not (await service(superuser).settings()).enabled


async def test_a_client_is_deleted_only_when_it_is_switched_off(superuser: Site) -> None:
    """ADR 0084 point 7: two deliberate steps, disable and then delete."""
    client_id, _ = await register(superuser)
    url = f"/admin/api/clients/{client_id}"
    assert f"{url}/delete" not in (await superuser.client.get(url)).text  # still on

    refused = await superuser.client.post(f"{url}/delete", headers=SAME_ORIGIN)
    await superuser.client.post(f"{url}/enable", data={}, headers=SAME_ORIGIN)
    offered = (await superuser.client.get(url)).text
    deleted = await superuser.client.post(f"{url}/delete", headers=SAME_ORIGIN)

    assert refused.status_code == 409
    assert "Schakel de API-client eerst uit" in refused.text
    assert f"{url}/delete" in offered
    assert deleted.headers["location"] == "/admin/api?done=client_deleted"
    assert (await superuser.client.get(url)).status_code == 404
    assert client_id not in (await superuser.client.get("/admin/api")).text


def test_the_client_list_narrows_by_search_and_organisation() -> None:
    def client(client_id: str, name: str, organisation: str) -> ApiClient:
        return ApiClient(
            id=None,
            client_id=client_id,
            name=name,
            organisation=organisation,
            groups=frozenset(),
            scopes=frozenset(),
            networks=(),
            enabled=True,
            secret_hash="0" * 64,
            secret_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    clients = [client("op_a", "Bot Noord", "demo"), client("op_b", "Bot Zuid", "andere")]
    names = {"demo": "Demo-acme", "andere": "Andere acme"}

    rows, filters = narrow_clients(clients, names, "  NOORD ", "")
    assert [c.client_id for c in rows] == ["op_a"]
    assert filters.active
    rows, filters = narrow_clients(clients, names, "", "andere")
    assert [c.client_id for c in rows] == ["op_b"]
    rows, filters = narrow_clients(clients, names, "", "elders")  # unknown: dropped
    assert len(rows) == 2
    assert not filters.active
    assert [o.value for o in filters.organisations] == ["andere", "demo"]


async def test_the_client_list_counts_and_never_shows_the_search_again(site: Site) -> None:
    await site.login_as(SUPER)
    await register(site)

    found = (await site.client.get("/admin/api?q=wxcc&lang=en")).text
    missed = (await site.client.get("/admin/api?q=geheim-zoekwoord&lang=en")).text

    assert "Clients that match: 1 of 1" in found
    assert "No client matches this filter." in missed
    assert "geheim-zoekwoord" not in missed
