"""The ``StaffDirectory`` of the bundled Keycloak, over its admin REST API (ADR 0095).

The portal logs in as the service account ``openplanner-directory`` (client credentials)
whose only roles are ``manage-users`` and ``view-realm`` of the realm. It reads and writes
people and their ``entitlements`` attribute, which the realm maps to the claim of ADR 0049.
Every failure is a ``DirectoryError`` with a short code; the provider's own message is never
passed on, because it may quote an address.

    token  POST  {host}/realms/{realm}/protocol/openid-connect/token
    users  GET   {admin}/users                     POST {admin}/users
    user   GET   {admin}/users/{id}                PUT  {admin}/users/{id}
    mail   PUT   {admin}/users/{id}/execute-actions-email?lifespan=N
"""

import time
from collections.abc import Collection
from typing import Any

import httpx

from app.domain.staff_directory import (
    ADMIN_URL_PATTERN,
    DirectoryError,
    DirectoryUser,
    NewStaffMember,
    StaffAction,
)

ENTITLEMENTS_ATTRIBUTE = "entitlements"
"""The user attribute the realm template maps to ``openplanner_entitlements``."""


_ACTIONS = {
    StaffAction.UPDATE_PASSWORD: "UPDATE_PASSWORD",
    StaffAction.VERIFY_EMAIL: "VERIFY_EMAIL",
}
_PAGE = 500
"""How many people one listing asks for; a test installation has a handful."""
_TIMEOUT = 10.0
_TOKEN_MARGIN = 10.0


class KeycloakDirectory:
    """Staff of one Keycloak realm, managed through its admin API."""

    def __init__(
        self,
        admin_url: str,
        client_id: str,
        client_secret: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Remember where the realm is and who we are.

        Args:
            admin_url: The realm's admin API, ``…/admin/realms/<realm>``.
            client_id: The service account's client id.
            client_secret: Its secret; never logged.
            client: An HTTP client, for tests.

        Raises:
            ValueError: If ``admin_url`` is not a realm admin URL.
        """
        found = ADMIN_URL_PATTERN.match(admin_url.rstrip("/"))
        if found is None:
            raise ValueError("KEYCLOAK_ADMIN_URL must end in /admin/realms/<realm>")
        self._admin = admin_url.rstrip("/")
        self._token_url = (
            f"{found.group('host')}/realms/{found.group('realm')}/protocol/openid-connect/token"
        )
        self._client_id = client_id
        self._secret = client_secret
        self._http = client or httpx.AsyncClient(timeout=_TIMEOUT)
        self._token: str | None = None
        self._expires = 0.0

    async def _bearer(self) -> dict[str, str]:
        """Return the Authorization header, asking for a token when the last one ran out."""
        if self._token is None or time.monotonic() >= self._expires:
            try:
                response = await self._http.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._secret,
                    },
                )
            except httpx.HTTPError as error:
                raise DirectoryError("unreachable") from error
            if response.status_code != 200:
                raise DirectoryError("refused")
            body = response.json()
            self._token = str(body["access_token"])
            self._expires = time.monotonic() + float(body.get("expires_in", 60)) - _TOKEN_MARGIN
        return {"Authorization": f"Bearer {self._token}"}

    async def _call(
        self, method: str, path: str, *, ok: tuple[int, ...] = (200, 204), **kwargs: Any
    ) -> httpx.Response:
        """Call the admin API; map every failure to a code."""
        headers = await self._bearer()
        try:
            response = await self._http.request(
                method, f"{self._admin}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as error:
            raise DirectoryError("unreachable") from error
        if response.status_code in ok:
            return response
        if response.status_code in (401, 403):
            raise DirectoryError("refused")
        if response.status_code == 404:
            raise DirectoryError("not_found")
        if response.status_code == 409:
            raise DirectoryError("conflict")
        raise DirectoryError("failed")

    async def reachable(self) -> bool:
        """Return whether a one-person listing works with the service account."""
        try:
            await self._call("GET", "/users", params={"max": 1})
        except DirectoryError:
            return False
        return True

    async def list_users(self) -> list[DirectoryUser]:
        """Return the people of the realm."""
        response = await self._call("GET", "/users", params={"max": _PAGE})
        return [_user(item) for item in response.json()]

    async def get_user(self, user_id: str) -> DirectoryUser | None:
        """Return one person, or ``None`` when the provider does not know the id."""
        try:
            response = await self._call("GET", f"/users/{user_id}")
        except DirectoryError as error:
            if error.code == "not_found":
                return None
            raise
        return _user(response.json())

    async def create_user(self, new: NewStaffMember) -> DirectoryUser:
        """Create a person who must set a password and confirm the address at first use."""
        response = await self._call(
            "POST",
            "/users",
            ok=(201,),
            json={
                "username": new.email,
                "email": new.email,
                "firstName": new.first_name,
                "lastName": new.last_name,
                "enabled": True,
                "emailVerified": False,
                "requiredActions": ["UPDATE_PASSWORD", "VERIFY_EMAIL"],
            },
        )
        user_id = response.headers.get("Location", "").rsplit("/", 1)[-1]
        created = await self.get_user(user_id) if user_id else None
        if created is None:
            raise DirectoryError("failed")
        return created

    async def _representation(self, user_id: str) -> dict[str, Any]:
        """Return the person's full representation, which a PUT must send back whole."""
        response = await self._call("GET", f"/users/{user_id}")
        body: dict[str, Any] = response.json()
        return body

    async def set_entitlements(self, user_id: str, values: Collection[str]) -> None:
        """Replace the entitlement attribute, keeping every other attribute."""
        body = await self._representation(user_id)
        attributes = dict(body.get("attributes") or {})
        attributes[ENTITLEMENTS_ATTRIBUTE] = sorted(values)
        await self._call("PUT", f"/users/{user_id}", json={**body, "attributes": attributes})

    async def set_enabled(self, user_id: str, enabled: bool) -> None:
        """Enable or disable the person."""
        body = await self._representation(user_id)
        await self._call("PUT", f"/users/{user_id}", json={**body, "enabled": enabled})

    async def send_actions_email(
        self, user_id: str, actions: Collection[StaffAction], lifespan_seconds: int
    ) -> None:
        """Mail the person a link; a mail the provider cannot send is code ``mail``."""
        try:
            await self._call(
                "PUT",
                f"/users/{user_id}/execute-actions-email",
                params={"lifespan": lifespan_seconds},
                json=[_ACTIONS[a] for a in actions],
            )
        except DirectoryError as error:
            if error.code == "failed":
                raise DirectoryError("mail") from error
            raise


def _user(item: dict[str, Any]) -> DirectoryUser:
    """Translate Keycloak's representation of a person."""
    attributes = item.get("attributes") or {}
    return DirectoryUser(
        id=str(item["id"]),
        username=str(item.get("username", "")),
        email=str(item.get("email", "")),
        first_name=str(item.get("firstName", "")),
        last_name=str(item.get("lastName", "")),
        enabled=bool(item.get("enabled", True)),
        entitlements=frozenset(attributes.get(ENTITLEMENTS_ATTRIBUTE, [])),
    )
