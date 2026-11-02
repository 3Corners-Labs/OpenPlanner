"""The Keycloak adapter of the staff directory against a mocked admin REST API (ADR 0095)."""

import json

import httpx
import pytest

from app.adapters.keycloak_directory import KeycloakDirectory
from app.domain.staff_directory import DirectoryError, NewStaffMember, StaffAction

ADMIN_URL = "http://kc.test/admin/realms/openplanner"
TOKEN_URL = "http://kc.test/realms/openplanner/protocol/openid-connect/token"

USER = {
    "id": "u-1",
    "username": "olaf@example.test",
    "email": "olaf@example.test",
    "firstName": "Olaf",
    "lastName": "Org",
    "enabled": True,
    "attributes": {"entitlements": ["openplanner:org_admin:demo"], "other": ["keep"]},
}


class Server:
    """A minimal Keycloak: records requests, answers from ``routes``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        path = request.url.path.removeprefix("/admin/realms/openplanner")
        return self.routes.get((request.method, path), httpx.Response(404))


def directory(server: Server) -> KeycloakDirectory:
    return KeycloakDirectory(
        ADMIN_URL,
        "openplanner-directory",
        "secret-value",
        client=httpx.AsyncClient(transport=httpx.MockTransport(server)),
    )


async def test_it_asks_for_a_token_with_the_service_account_and_sends_it() -> None:
    server = Server()
    server.routes[("GET", "/users")] = httpx.Response(200, json=[USER])

    users = await directory(server).list_users()

    assert [u.id for u in users] == ["u-1"]
    assert users[0].entitlements == {"openplanner:org_admin:demo"}
    token_call, users_call = server.requests
    assert b"grant_type=client_credentials" in token_call.content
    assert users_call.headers["authorization"] == "Bearer t"


async def test_it_creates_a_person_without_a_password() -> None:
    server = Server()
    server.routes[("POST", "/users")] = httpx.Response(
        201, headers={"Location": f"{ADMIN_URL}/users/u-9"}
    )
    server.routes[("GET", "/users/u-9")] = httpx.Response(200, json={**USER, "id": "u-9"})

    created = await directory(server).create_user(
        NewStaffMember("olaf@example.test", "Olaf", "Org")
    )

    assert created.id == "u-9"
    body = json.loads(
        next(
            r for r in server.requests if r.method == "POST" and r.url.path.endswith("/users")
        ).content
    )
    assert body["username"] == body["email"] == "olaf@example.test"
    assert body["requiredActions"] == ["UPDATE_PASSWORD", "VERIFY_EMAIL"]
    assert "credentials" not in body


async def test_an_address_in_use_is_a_conflict() -> None:
    server = Server()
    server.routes[("POST", "/users")] = httpx.Response(409)

    with pytest.raises(DirectoryError) as caught:
        await directory(server).create_user(NewStaffMember("olaf@example.test", "Olaf", "Org"))

    assert caught.value.code == "conflict"


async def test_changing_entitlements_keeps_the_other_attributes() -> None:
    server = Server()
    server.routes[("GET", "/users/u-1")] = httpx.Response(200, json=USER)
    server.routes[("PUT", "/users/u-1")] = httpx.Response(204)

    await directory(server).set_entitlements("u-1", ["openplanner:auditor:demo"])

    sent = json.loads(next(r for r in server.requests if r.method == "PUT").content)
    assert sent["attributes"]["entitlements"] == ["openplanner:auditor:demo"]
    assert sent["attributes"]["other"] == ["keep"]
    assert sent["email"] == "olaf@example.test"


async def test_disabling_a_person_sets_enabled_false() -> None:
    server = Server()
    server.routes[("GET", "/users/u-1")] = httpx.Response(200, json=USER)
    server.routes[("PUT", "/users/u-1")] = httpx.Response(204)

    await directory(server).set_enabled("u-1", False)

    assert (
        json.loads(next(r for r in server.requests if r.method == "PUT").content)["enabled"]
        is False
    )


async def test_the_actions_mail_names_the_actions_and_the_lifespan() -> None:
    server = Server()
    server.routes[("PUT", "/users/u-1/execute-actions-email")] = httpx.Response(204)

    await directory(server).send_actions_email("u-1", [StaffAction.UPDATE_PASSWORD], 7 * 86400)

    call = server.requests[-1]
    assert call.url.params["lifespan"] == str(7 * 86400)
    assert json.loads(call.content) == ["UPDATE_PASSWORD"]


async def test_a_mail_the_provider_cannot_send_is_reported_without_its_message() -> None:
    server = Server()
    server.routes[("PUT", "/users/u-1/execute-actions-email")] = httpx.Response(
        500, json={"errorMessage": "smtp to olaf@example.test failed"}
    )

    with pytest.raises(DirectoryError) as caught:
        await directory(server).send_actions_email("u-1", [StaffAction.UPDATE_PASSWORD], 60)

    assert caught.value.code == "mail"
    assert "olaf" not in str(caught.value)


async def test_an_unknown_person_is_none() -> None:
    assert await directory(Server()).get_user("nobody") is None


async def test_an_unreachable_provider_is_an_error_with_a_code() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    broken = KeycloakDirectory(
        ADMIN_URL, "c", "s", client=httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )

    assert await broken.reachable() is False
    with pytest.raises(DirectoryError) as caught:
        await broken.list_users()
    assert caught.value.code == "unreachable"


async def test_a_refused_service_account_is_an_error() -> None:
    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    denied = KeycloakDirectory(
        ADMIN_URL, "c", "s", client=httpx.AsyncClient(transport=httpx.MockTransport(deny))
    )

    with pytest.raises(DirectoryError) as caught:
        await denied.list_users()
    assert caught.value.code == "refused"
