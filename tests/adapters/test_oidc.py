"""Contract test of the OIDC adapter against an in-memory provider with real RSA keys."""

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import structlog.testing
from cryptography.hazmat.primitives.asymmetric import rsa

from app.adapters.oidc import ClaimMapping, OidcConfig, OidcIdentityProvider, principal_from_claims
from app.domain.authorization import Role
from app.domain.ports import IdentityError

ISSUER = "https://idp.example.nl"
CLIENT = "openplanner-admin"
MAPPING = ClaimMapping(
    role_claim="roles",
    organisation_claim="org",
    group_claim="groups",
    role_values={
        "op-superuser": Role.SUPERUSER,
        "op-org-admin": Role.ORG_ADMIN,
        "op-group-admin": Role.GROUP_ADMIN,
    },
)
REDIRECT = "https://afspraak.example.nl/admin/callback"


class FakeProvider:
    """Discovery, token and JWKS endpoints; issues ID tokens with configurable claims."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.claims: dict[str, Any] = {}
        self.header: dict[str, Any] = {}
        self.token_status = 200
        self.received: dict[str, Any] = {}
        self.jwks_calls = 0
        self.extra_metadata: dict[str, Any] = {}

    def jwk(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**public, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT,
            "sub": "user-1",
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "name": "Ada Admin",
            "roles": ["op-group-admin"],
            "groups": ["demo"],
        }
        claims.update(self.claims)
        claims = {k: v for k, v in claims.items() if v is not None}
        headers = {"kid": self.kid, **self.header}
        return jwt.encode(claims, self.key, algorithm="RS256", headers=headers)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    **self.extra_metadata,
                },
            )
        if request.url.path == "/jwks":
            self.jwks_calls += 1
            return httpx.Response(200, json={"keys": [self.jwk()]})
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            self.received = {k: v[0] for k, v in form.items()}
            self.received["authorization"] = request.headers.get("authorization")
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": self.id_token(self.nonce)})
        return httpx.Response(404)

    nonce = ""


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def oidc(provider: FakeProvider) -> AsyncIterator[OidcIdentityProvider]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        config = OidcConfig(
            discovery_url=f"{ISSUER}/.well-known/openid-configuration",
            client_id=CLIENT,
            client_secret="s3cret",
            scopes="openid profile",
            mapping=MAPPING,
        )
        yield OidcIdentityProvider(config, http)


async def login(oidc: OidcIdentityProvider, provider: FakeProvider, nonce: str | None = None):  # type: ignore[no-untyped-def]
    start = await oidc.start_login(REDIRECT)
    provider.nonce = start.nonce if nonce is None else nonce
    principal = await oidc.finish_login(
        code="abc", redirect_uri=REDIRECT, code_verifier=start.code_verifier, nonce=start.nonce
    )
    return start, principal


async def test_full_flow_with_pkce(oidc: OidcIdentityProvider, provider: FakeProvider) -> None:
    start, principal = await login(oidc, provider)

    query = parse_qs(urlsplit(start.url).query)
    assert start.url.startswith(f"{ISSUER}/authorize?")
    assert query["response_type"] == ["code"]
    assert query["state"] == [start.state]
    assert query["code_challenge_method"] == ["S256"]
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(provider.received["code_verifier"].encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge"] == [challenge]
    assert provider.received["redirect_uri"] == REDIRECT
    assert provider.received["authorization"].startswith("Basic ")
    assert principal.subject == "user-1"
    assert principal.roles == {Role.GROUP_ADMIN}
    assert principal.groups == {"demo"}
    assert "s3cret" not in repr(oidc._config)  # noqa: SLF001
    assert start.code_verifier not in repr(start)


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example"},
        {"aud": "someone-else"},
        {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
        {"sub": None},
        {"aud": [CLIENT, "other"]},  # several audiences without azp
        {"azp": "other-client"},
    ],
)
async def test_invalid_claims_are_refused(
    oidc: OidcIdentityProvider, provider: FakeProvider, claims: dict[str, Any]
) -> None:
    provider.claims = claims

    with pytest.raises(IdentityError, match="invalid_token"):
        await login(oidc, provider)


async def test_several_audiences_with_matching_azp_are_accepted(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    provider.claims = {"aud": [CLIENT, "other"], "azp": CLIENT}

    _, principal = await login(oidc, provider)

    assert principal.subject == "user-1"


async def test_nonce_mismatch_is_refused(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    with pytest.raises(IdentityError, match="nonce_mismatch"):
        await login(oidc, provider, nonce="replayed")


async def test_forged_signature_is_refused(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    start = await oidc.start_login(REDIRECT)
    provider.nonce = start.nonce
    good_key = provider.key
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = provider.id_token(start.nonce)  # signed by an attacker's key, same kid
    provider.key = good_key

    with pytest.raises(IdentityError, match="invalid_token"):
        await oidc._verify(token, await oidc._discover())  # noqa: SLF001


@pytest.mark.parametrize("alg", ["none", "HS256"])
async def test_unsafe_algorithms_are_refused(
    oidc: OidcIdentityProvider, provider: FakeProvider, alg: str
) -> None:
    metadata = await oidc._discover()  # noqa: SLF001
    claims = {
        "iss": ISSUER,
        "aud": CLIENT,
        "sub": "x",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    if alg == "none":
        token = jwt.encode(claims, None, algorithm="none")
    else:
        # Classic confusion attack: HMAC "signed" with the public key.
        token = jwt.encode(
            claims, "public-key-material-used-as-hmac-secret-0123456789", algorithm="HS256"
        )

    with pytest.raises(IdentityError, match="invalid_token"):
        await oidc._verify(token, metadata)  # noqa: SLF001


async def test_key_rotation_refetches_the_key_set(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    await login(oidc, provider)
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider.kid = "key-2"

    _, principal = await login(oidc, provider)

    assert principal.subject == "user-1"
    assert provider.jwks_calls == 2


async def test_failed_token_exchange(oidc: OidcIdentityProvider, provider: FakeProvider) -> None:
    provider.token_status = 400

    with pytest.raises(IdentityError, match="token_exchange_failed"):
        await login(oidc, provider)


@pytest.fixture
async def unreachable() -> AsyncIterator[OidcIdentityProvider]:
    """A provider whose discovery document answers 503."""

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        yield OidcIdentityProvider(
            OidcConfig(
                f"{ISSUER}/.well-known/openid-configuration", CLIENT, "s", "openid", MAPPING
            ),
            http,
        )


async def test_unreachable_discovery(unreachable: OidcIdentityProvider) -> None:
    with pytest.raises(IdentityError, match="discovery_failed"):
        await unreachable.start_login(REDIRECT)


@pytest.mark.parametrize(
    ("claims", "roles", "orgs", "groups"),
    [
        ({"roles": "op-superuser"}, {Role.SUPERUSER}, set(), set()),
        (
            {"roles": "op-org-admin op-group-admin", "org": "demo,andere"},
            {Role.ORG_ADMIN, Role.GROUP_ADMIN},
            {"demo", "andere"},
            set(),
        ),
        (
            {"roles": ["unknown", "op-group-admin"], "groups": ["demo"]},
            {Role.GROUP_ADMIN},
            set(),
            {"demo"},
        ),
        ({"roles": {"not": "a list"}, "groups": 42}, set(), set(), set()),
        ({}, set(), set(), set()),
    ],
)
def test_claim_mapping(
    claims: dict[str, Any], roles: set[Role], orgs: set[str], groups: set[str]
) -> None:
    principal = principal_from_claims({"sub": "u", **claims}, MAPPING)

    assert (principal.roles, principal.organisations, principal.groups) == (roles, orgs, groups)
    assert principal.display_name == "u"


async def test_logout_url_uses_the_end_session_endpoint(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    provider.extra_metadata = {"end_session_endpoint": f"{ISSUER}/logout"}

    url = await oidc.logout_url("https://afspraak.example.nl/admin/logged-out")

    assert url is not None
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(f"{ISSUER}/logout?")
    assert query == {
        "client_id": [CLIENT],
        "post_logout_redirect_uri": ["https://afspraak.example.nl/admin/logged-out"],
    }


async def test_logout_url_is_none_without_end_session_endpoint(
    oidc: OidcIdentityProvider,
) -> None:
    assert await oidc.logout_url("https://x/admin/logged-out") is None


async def test_logout_url_is_none_when_discovery_fails(
    unreachable: OidcIdentityProvider,
) -> None:
    assert await unreachable.logout_url("https://x/admin/logged-out") is None


def test_other_role_values_are_kept_for_custom_roles_within_bounds() -> None:
    # ADR 0039: custom roles link to role claim values; only well-shaped ones, at most 20.
    others = [f"op-team-{n:02d}" for n in range(25)]
    principal = principal_from_claims(
        {"sub": "s", "roles": ["op-group-admin", "<bad value>", *others]}, MAPPING
    )

    assert principal.roles == frozenset({Role.GROUP_ADMIN})
    assert principal.role_claims == frozenset(others[:20])
    assert principal.custom_roles == frozenset()


# --- lockout diagnostics (ADR 0042 §8 rule 4) ----------------------------------------------

CLAIM_VALUES = ("Jan Jansen", "jan@example.nl", "op-unknown-role", "secret-org", "dept-7")


def _leaked(events: list[dict[str, Any]]) -> list[str]:
    """Return the claim values that appear anywhere in the captured log events."""
    logged = json.dumps(events, default=str)
    return [value for value in CLAIM_VALUES if value in logged]


def test_a_missing_role_claim_is_logged_by_name_without_any_claim_value() -> None:
    claims = {"sub": "u", "name": "Jan Jansen", "email": "jan@example.nl", "dept": "dept-7"}

    with structlog.testing.capture_logs() as events:
        principal = principal_from_claims(claims, MAPPING)

    assert principal.roles == frozenset()
    (event,) = [e for e in events if e["event"] == "oidc.no_role"]
    assert event["log_level"] == "warning"
    assert event["missing_claim"] == "roles"
    assert event["claims_present"] == ["dept", "email", "name", "sub"]
    assert _leaked(events) == []


def test_role_values_that_match_nothing_are_counted_never_shown() -> None:
    claims = {"sub": "u", "roles": ["op-unknown-role", "other"], "org": "secret-org"}

    with structlog.testing.capture_logs() as events:
        principal_from_claims(claims, MAPPING)

    (event,) = [e for e in events if e["event"] == "oidc.no_role"]
    assert event["missing_claim"] is None
    assert event["role_values_seen"] == 2
    assert event["claims_present"] == ["org", "roles", "sub"]
    assert _leaked(events) == []


@pytest.mark.parametrize(("role", "claim"), [("op-org-admin", "org"), ("op-group-admin", "groups")])
def test_a_scoped_role_without_its_scope_claim_is_logged(role: str, claim: str) -> None:
    with structlog.testing.capture_logs() as events:
        principal_from_claims({"sub": "u", "roles": [role]}, MAPPING)

    (event,) = [e for e in events if e["event"] == "oidc.no_scope"]
    assert event["missing_claim"] == claim
    assert event["claims_present"] == ["roles", "sub"]


def test_a_resolved_login_logs_no_diagnostics() -> None:
    with structlog.testing.capture_logs() as events:
        principal_from_claims({"sub": "u", "roles": "op-superuser"}, MAPPING)

    assert events == []


def test_claim_names_in_the_diagnostics_are_bounded() -> None:
    claims: dict[str, Any] = {"sub": "u", **{f"c{i:03}" + "x" * 100: i for i in range(80)}}

    with structlog.testing.capture_logs() as events:
        principal_from_claims(claims, MAPPING)

    (event,) = events
    assert len(event["claims_present"]) == 50
    assert max(len(name) for name in event["claims_present"]) <= 64
