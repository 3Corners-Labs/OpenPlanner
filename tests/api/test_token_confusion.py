"""Token confusion and wrong input types on the machine, widget and admin surfaces.

ASVS V9.2.2, V13.4.2 and V5.4.2: a token only works where it was issued for (an ID token is not
a machine API token, a machine token is not an admin session), and a value of the wrong JSON
type is a 422 that never repeats what was sent. Fixtures come from the machine API and widget
tests.
"""

import secrets

import httpx
import jwt
import pytest

from tests.api.test_agent_api import BASE as AGENT_BASE
from tests.api.test_agent_api import Agent
from tests.api.test_agent_api import agent as machine_api
from tests.api.test_widget_api import ADA, SITE
from tests.api.test_widget_api import BASE as WIDGET_BASE
from tests.api.test_widget_api import site as widget_site

agent = machine_api  # the app with the machine API on, as its own tests run it
site = widget_site  # the app with the demo group embeddable, as the widget tests run it
CANARY = "Canary-Pii-7731"
"""A recognisable value standing for personal data: it must never come back in an answer."""


def _id_token(**claims: object) -> str:
    """Return a signed OIDC-style ID token, as an identity provider issues for the admin login."""
    payload = {"iss": "https://idp.example", "sub": "sub-super", "aud": "openplanner", **claims}
    return jwt.encode(payload, secrets.token_bytes(32), algorithm="HS256")


@pytest.mark.parametrize("path", ["/me", "/groups", "/groups/demo/availability"])
async def test_an_id_token_is_no_bearer_token_of_the_machine_api(agent: Agent, path: str) -> None:
    response = await agent.client.get(
        f"{AGENT_BASE}{path}",
        params={"from": "2026-01-01", "to": "2026-01-02"},
        headers={"authorization": f"Bearer {_id_token()}"},
    )

    assert (response.status_code, response.json()) == (401, {"error": "invalid_token"})
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_an_id_token_is_no_client_secret_at_the_token_endpoint(agent: Agent) -> None:
    issued = await agent.register()
    forged = issued.__class__(client=issued.client, secret=_id_token())

    response = await agent.token(forged)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_client"}


@pytest.mark.parametrize("carry", ["cookie", "authorization"])
async def test_a_machine_token_is_no_admin_session(agent: Agent, carry: str) -> None:
    token = (await agent.bearer(await agent.register()))["authorization"].split(" ")[1]
    headers = (
        {"cookie": f"op_admin={token}"}
        if carry == "cookie"
        else {"authorization": f"Bearer {token}"}
    )

    response = await agent.client.get("/admin/", headers=headers)

    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]
    assert token not in response.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    "body",
    [
        {**ADA, "start": 20260102},
        {**ADA, "first_name": ["Ada", CANARY]},
        {**ADA, "last_name": {"name": CANARY}},
        {**ADA, "email": [CANARY]},
        {**ADA, "email": 12345},
        {**ADA, "phone": {"number": CANARY}},
        {**ADA, "answers": [CANARY]},
        {**ADA, "answers": {"onderwerp": {"x": CANARY}}},
        {**ADA, "answers": {"onderwerp": 7}},
        {**ADA, "website": [CANARY]},
        [CANARY],
        CANARY,
        None,
        12.5,
    ],
    ids=lambda body: type(body).__name__ + str(sorted(body) if isinstance(body, dict) else ""),
)
async def test_a_widget_body_of_the_wrong_types_is_a_422_without_the_input(
    site: httpx.AsyncClient, body: object
) -> None:
    response = await site.post(f"{WIDGET_BASE}/bookings", json=body, headers={"origin": SITE})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert CANARY not in response.text
    assert "Lovelace" not in response.text


@pytest.mark.parametrize(
    "params",
    [
        {"from": CANARY, "to": "2026-01-02"},
        {"from": ["2026-01-01", CANARY], "to": "2026-01-02"},
        {"from": "2026-01-01", "to": "[1,2]" + CANARY},
        {"from": "2026-01-01", "to": "2026-01-02", "lang": CANARY},
        {"from": "2026-01-01"},
    ],
    ids=["text", "list", "array", "language", "missing"],
)
async def test_machine_api_query_of_the_wrong_type_is_a_422_without_the_input(
    agent: Agent, params: dict[str, object]
) -> None:
    headers = await agent.bearer(await agent.register())

    response = await agent.client.get(
        f"{AGENT_BASE}/groups/demo/availability",
        params=params,  # type: ignore[arg-type]
        headers=headers,
    )

    assert (response.status_code, response.json()) == (422, {"error": "invalid_request"})
    assert CANARY not in response.text


async def test_a_machine_api_passphrase_that_is_too_short_or_missing_is_a_422_without_it(
    agent: Agent,
) -> None:
    issued = await agent.register(scopes=["configuration:transfer"])
    headers = await agent.bearer(issued)

    short = await agent.client.get(
        f"{AGENT_BASE}/configuration", headers={**headers, "x-passphrase": CANARY}
    )
    missing = await agent.client.get(f"{AGENT_BASE}/configuration", headers=headers)

    assert short.status_code == missing.status_code == 422
    assert CANARY not in short.text + missing.text
