"""Contract test of the OIDC adapter against an in-memory provider with real RSA keys."""

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

import httpx
import jwt
import pytest
import structlog.testing
from cryptography.hazmat.primitives.asymmetric import rsa

from app.adapters.oidc import (
    ClaimMapping,
    Guard,
    OidcConfig,
    OidcIdentityProvider,
    claim_mapping,
    client_secret_basic,
    login_claims,
)
from app.adapters.signing_keys import EcSigningKeys
from app.domain.authorization import Role
from app.domain.identity_providers import ClientAuth
from app.domain.ports import IdentityError
from app.domain.settings_registry import lookup

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
        self.metadata_issuer = ISSUER

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
                    "issuer": self.metadata_issuer,
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
    principal = login_claims({"sub": "u", **claims}, MAPPING)

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
    principal = login_claims(
        {"sub": "s", "roles": ["op-group-admin", "<bad value>", *others]}, MAPPING
    )

    assert principal.roles == frozenset({Role.GROUP_ADMIN})
    assert principal.role_claims == frozenset(others[:20])


def test_entitlement_values_with_the_prefix_are_kept_and_the_rest_ignored() -> None:
    # ADR 0049: the adapter extracts; what a value means is decided with the directory.
    claims = login_claims(
        {
            "sub": "s",
            "openplanner_entitlements": [
                "openplanner:org_admin:gem-a",
                "openplanner-acc:org_admin:gem-a",
                "cn=admins",
            ],
        },
        MAPPING,
    )

    assert claims.entitlements == frozenset({"openplanner:org_admin:gem-a"})
    assert claims.roles == frozenset()


def test_a11_a_claim_flood_refuses_the_login() -> None:
    flood = {"sub": "s", "openplanner_entitlements": [f"openplanner:x:{n}" for n in range(5000)]}

    with pytest.raises(IdentityError) as caught:
        login_claims(flood, MAPPING)

    assert caught.value.code == "too_many_entitlements"
    assert (
        len(
            login_claims(
                {**flood, "openplanner_entitlements": flood["openplanner_entitlements"][:200]},
                MAPPING,
            ).entitlements
        )
        == 200
    )


# --- lockout diagnostics (ADR 0042 §8 rule 4) ----------------------------------------------

CLAIM_VALUES = ("Jan Jansen", "jan@example.nl", "op-unknown-role", "secret-org", "dept-7")


def _leaked(events: list[dict[str, Any]]) -> list[str]:
    """Return the claim values that appear anywhere in the captured log events."""
    logged = json.dumps(events, default=str)
    return [value for value in CLAIM_VALUES if value in logged]


def test_a_missing_role_claim_is_logged_by_name_without_any_claim_value() -> None:
    claims = {"sub": "u", "name": "Jan Jansen", "email": "jan@example.nl", "dept": "dept-7"}

    with structlog.testing.capture_logs() as events:
        principal = login_claims(claims, MAPPING)

    assert principal.roles == frozenset()
    (event,) = [e for e in events if e["event"] == "oidc.no_role"]
    assert event["log_level"] == "warning"
    assert event["missing_claim"] == "roles"
    assert event["claims_present"] == ["dept", "email", "name", "sub"]
    assert _leaked(events) == []


def test_role_values_that_match_nothing_are_counted_never_shown() -> None:
    claims = {"sub": "u", "roles": ["op-unknown-role", "other"], "org": "secret-org"}

    with structlog.testing.capture_logs() as events:
        login_claims(claims, MAPPING)

    (event,) = [e for e in events if e["event"] == "oidc.no_role"]
    assert event["missing_claim"] is None
    assert event["role_values_seen"] == 2
    assert event["claims_present"] == ["org", "roles", "sub"]
    assert _leaked(events) == []


@pytest.mark.parametrize(("role", "claim"), [("op-org-admin", "org"), ("op-group-admin", "groups")])
def test_a_scoped_role_without_its_scope_claim_is_logged(role: str, claim: str) -> None:
    with structlog.testing.capture_logs() as events:
        login_claims({"sub": "u", "roles": [role]}, MAPPING)

    (event,) = [e for e in events if e["event"] == "oidc.no_scope"]
    assert event["missing_claim"] == claim
    assert event["claims_present"] == ["roles", "sub"]


def test_a_resolved_login_logs_no_diagnostics() -> None:
    with structlog.testing.capture_logs() as events:
        login_claims({"sub": "u", "roles": "op-superuser"}, MAPPING)

    assert events == []


def test_claim_names_in_the_diagnostics_are_bounded() -> None:
    claims: dict[str, Any] = {"sub": "u", **{f"c{i:03}" + "x" * 100: i for i in range(80)}}

    with structlog.testing.capture_logs() as events:
        login_claims(claims, MAPPING)

    (event,) = events
    assert len(event["claims_present"]) == 50
    assert max(len(name) for name in event["claims_present"]) <= 64


# The login mapping as settings, and its dry run (ADR 0042 §8).

TRIAL = {
    "oidc_role_claim": "realm_roles",
    "oidc_organisation_claim": "org",
    "oidc_group_claim": "groups",
    "oidc_role_superuser": "op-root",
    "oidc_role_org_admin": "op-org-admin",
    "oidc_role_group_admin": "op-group-admin",
    "oidc_role_auditor": "op-audit",
    "oidc_role_agent": "op-agent",
}


async def test_a_fresh_login_asks_the_provider_to_authenticate_again(
    oidc: OidcIdentityProvider,
) -> None:
    query = parse_qs(urlsplit((await oidc.start_login(REDIRECT, fresh=True)).url).query)
    plain = parse_qs(urlsplit((await oidc.start_login(REDIRECT)).url).query)

    assert (query["prompt"], query["max_age"]) == (["login"], ["0"])
    assert "prompt" not in plain


async def test_a_trial_login_maps_the_same_claims_twice(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    provider.claims = {"realm_roles": ["op-root"]}
    start = await oidc.start_login(REDIRECT, fresh=True)
    provider.nonce = start.nonce

    current, trial = await oidc.finish_trial_login(
        code="abc",
        redirect_uri=REDIRECT,
        code_verifier=start.code_verifier,
        nonce=start.nonce,
        trial=TRIAL,
    )

    assert current.roles == {Role.GROUP_ADMIN}  # the "roles" claim, current mapping
    assert trial.roles == {Role.SUPERUSER}  # the "realm_roles" claim, proposed mapping
    assert current.subject == trial.subject


def test_the_mapping_follows_the_settings_at_every_login() -> None:
    mapping = claim_mapping(TRIAL)

    assert mapping.role_claim == "realm_roles"
    assert mapping.role_values["op-agent"] is Role.AGENT


DEV_REALM = Path(__file__).resolve().parents[2] / "dev" / "keycloak" / "openplanner-realm.json"
"""The Keycloak realm of the ``dev`` compose profile (ADR 0013 §6)."""


def _default_mapping() -> ClaimMapping:
    """Return the mapping of the login settings at their defaults (what a fresh install uses)."""
    keys = (
        "oidc_role_claim",
        "oidc_organisation_claim",
        "oidc_group_claim",
        "oidc_role_superuser",
        "oidc_role_org_admin",
        "oidc_role_group_admin",
        "oidc_role_auditor",
        "oidc_role_agent",
    )
    values: dict[str, str] = {}
    for key in keys:
        spec = lookup(key)
        assert spec is not None, key
        values[key] = str(spec.default)
    return claim_mapping(values)


def test_a_login_holding_every_role_value_holds_every_built_in_role() -> None:
    mapping = _default_mapping()
    claims = {
        "sub": "u",
        mapping.role_claim: sorted(mapping.role_values),
        mapping.organisation_claim: ["demo"],
        mapping.group_claim: ["demo"],
    }

    principal = login_claims(claims, mapping)

    assert principal.roles == frozenset(Role)
    assert (principal.organisations, principal.groups) == ({"demo"}, {"demo"})
    assert principal.role_claims == frozenset()  # every value is a built-in role, none is left


def test_the_dev_realm_super_user_holds_every_built_in_role() -> None:
    """``super`` in the dev realm walks the portal as an admin with every role at once."""
    realm = json.loads(DEV_REALM.read_text(encoding="utf-8"))
    mapping = _default_mapping()
    (sanne,) = [u for u in realm["users"] if u["username"] == "super"]
    attributes = sanne["attributes"]

    assert set(attributes[mapping.role_claim]) == set(mapping.role_values)
    assert attributes[mapping.organisation_claim] == ["demo"]
    assert attributes[mapping.group_claim] == ["demo"]


def test_b7_a_platform_sub_shaped_as_an_organisation_subject_refuses_the_login() -> None:
    # ADR 0083 point 16: `idp:` is reserved for the subjects of organisation providers.
    with pytest.raises(IdentityError) as refused:
        login_claims({"sub": "idp:gem-a:1234", "roles": ["op-org-admin"]}, MAPPING)

    assert refused.value.code == "reserved_subject"


def test_b6_an_organisation_provider_qualifies_the_subject() -> None:
    claims = login_claims({"sub": "1234"}, MAPPING, provider="gem-a")

    assert claims.subject == "idp:gem-a:1234"


async def test_the_platform_provider_keeps_the_bare_subject(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    _, claims = await login(oidc, provider)

    assert claims.subject == "user-1"


async def test_a_configured_provider_key_qualifies_every_login(provider: FakeProvider) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        config = OidcConfig(
            discovery_url=f"{ISSUER}/.well-known/openid-configuration",
            client_id=CLIENT,
            client_secret="s3cret",
            scopes="openid profile",
            mapping=MAPPING,
            provider_key="gem-a",
        )
        _, claims = await login(OidcIdentityProvider(config, http), provider)

    assert claims.subject == "idp:gem-a:user-1"


ORGANISATION_REALM = DEV_REALM.with_name("andere-realm.json")
"""The dev realm standing in for the organisation provider of ``andere`` (ADR 0083)."""


def test_the_organisation_dev_realm_sends_entitlements_only() -> None:
    """An organisation provider resolves in entitlements mode only (ADR 0083 point 2)."""
    realm = json.loads(ORGANISATION_REALM.read_text(encoding="utf-8"))
    (client,) = realm["clients"]
    claims = {m["config"]["claim.name"] for m in client["protocolMappers"]}

    assert claims == {"openplanner_entitlements"}
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert all(set(u["attributes"]) == {"entitlements"} for u in realm["users"])


def test_the_organisation_dev_realm_holds_a_user_per_abuse_case() -> None:
    """B1/B2 (rights outside the organisation), B3 (foreign domain), B4 (unverified email)."""
    users = {u["username"]: u for u in json.loads(ORGANISATION_REALM.read_text("utf-8"))["users"]}

    assert "openplanner:superuser" in users["overmoedig"]["attributes"]["entitlements"]
    assert "openplanner:org_admin:demo" in users["overmoedig"]["attributes"]["entitlements"]
    assert not users["buitendomein"]["email"].endswith("@andere.test")
    assert users["onbevestigd"]["emailVerified"] is False
    assert users["beheer"]["attributes"]["entitlements"] == ["openplanner:org_admin:andere"]


# --- organisation providers: private_key_jwt and the outbound guard (ADR 0083 step 2) ---------


def _organisation_config(**overrides: Any) -> OidcConfig:
    """The registration of an organisation provider, as the registry builds it."""
    values: dict[str, Any] = {
        "discovery_url": f"{ISSUER}/.well-known/openid-configuration",
        "client_id": CLIENT,
        "client_secret": "",
        "scopes": "openid profile email",
        "mapping": MAPPING,
        "provider_key": "gem-a",
        "client_auth": ClientAuth.PRIVATE_KEY_JWT,
        "guard": Guard(resolve=lambda host, port: ["8.8.8.8"]),
    }
    values.update(overrides)
    return OidcConfig(**values)


async def test_private_key_jwt_sends_a_signed_assertion_the_published_key_verifies(
    provider: FakeProvider,
) -> None:
    generated = EcSigningKeys().generate()
    config = _organisation_config(signing_key=(generated.kid, generated.private_pem))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        _, claims = await login(OidcIdentityProvider(config, http), provider)

    assert claims.subject == "idp:gem-a:user-1"
    assert provider.received["authorization"] is None
    assert provider.received["client_id"] == CLIENT
    assert provider.received["client_assertion_type"].endswith(":jwt-bearer")
    assertion = provider.received["client_assertion"]
    assert jwt.get_unverified_header(assertion)["kid"] == generated.kid
    public = jwt.PyJWK.from_dict({**generated.public_jwk, "kid": generated.kid}).key
    body = jwt.decode(assertion, public, algorithms=["ES256"], audience=f"{ISSUER}/token")
    assert (body["iss"], body["sub"]) == (CLIENT, CLIENT)
    assert body["exp"] - body["iat"] == 60
    assert body["jti"]


async def test_a_provider_without_a_key_cannot_exchange_the_code(provider: FakeProvider) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        oidc = OidcIdentityProvider(_organisation_config(), http)
        start = await oidc.start_login(REDIRECT)
        provider.nonce = start.nonce
        with pytest.raises(IdentityError) as failed:
            await oidc.finish_login(
                code="abc",
                redirect_uri=REDIRECT,
                code_verifier=start.code_verifier,
                nonce=start.nonce,
            )

    assert failed.value.code == "no_signing_key"
    assert provider.received == {}


async def test_b8_a_registered_providers_addresses_go_through_the_guard(
    provider: FakeProvider,
) -> None:
    config = _organisation_config(guard=Guard(resolve=lambda host, port: ["10.0.0.5"]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        with pytest.raises(IdentityError) as refused:
            await OidcIdentityProvider(config, http).start_login(REDIRECT)

    assert refused.value.code == "discovery_failed"
    assert provider.jwks_calls == 0


async def test_a_test_login_reports_claim_names_the_verified_flag_and_the_domain(
    provider: FakeProvider,
) -> None:
    generated = EcSigningKeys().generate()
    config = _organisation_config(signing_key=(generated.kid, generated.private_pem))
    provider.claims = {"email": "Ada@Gem-A.Example", "email_verified": True}
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        oidc = OidcIdentityProvider(config, http)
        start = await oidc.start_login(REDIRECT)
        provider.nonce = start.nonce
        report = await oidc.finish_test_login(
            code="abc", redirect_uri=REDIRECT, code_verifier=start.code_verifier, nonce=start.nonce
        )

    assert report.claims.subject == "idp:gem-a:user-1"
    assert "email" in report.claim_names
    assert "Ada@Gem-A.Example" not in repr(report)
    assert (report.email_verified, report.email_domain) == (True, "gem-a.example")


async def test_a_test_login_without_email_claims_reports_them_absent(
    provider: FakeProvider,
) -> None:
    generated = EcSigningKeys().generate()
    config = _organisation_config(signing_key=(generated.kid, generated.private_pem))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        oidc = OidcIdentityProvider(config, http)
        start = await oidc.start_login(REDIRECT)
        provider.nonce = start.nonce
        report = await oidc.finish_test_login(
            code="abc", redirect_uri=REDIRECT, code_verifier=start.code_verifier, nonce=start.nonce
        )

    assert (report.email_verified, report.email_domain) == (None, None)


# --- the issuer is pinned (ASVS V10.5.3; OIDC Discovery §4.3, Core §3.1.3.7) ------------------


async def test_a_discovery_document_with_another_issuer_is_refused(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    provider.metadata_issuer = "https://evil.example"
    with structlog.testing.capture_logs() as events:
        with pytest.raises(IdentityError, match="discovery_failed"):
            await oidc.start_login(REDIRECT)
    assert "oidc.issuer_mismatch" in [e["event"] for e in events]
    assert "evil.example" not in str(events)


async def test_a_trailing_slash_is_another_issuer(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    provider.metadata_issuer = f"{ISSUER}/"
    with pytest.raises(IdentityError, match="discovery_failed"):
        await oidc.start_login(REDIRECT)


async def test_metadata_and_token_agreeing_on_a_forged_issuer_are_refused(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    """Metadata and token agree on a forged issuer, and the signature is good: still refused."""
    provider.metadata_issuer = "https://evil.example"
    provider.claims = {"iss": "https://evil.example"}
    with pytest.raises(IdentityError, match="discovery_failed"):
        await login(oidc, provider)


async def test_the_pinned_issuer_decides_not_the_discovery_address(
    provider: FakeProvider,
) -> None:
    """An explicit ``expected_issuer`` (the internal discovery address differs) is what counts."""
    public = "https://login.example.nl/realms/x"
    provider.metadata_issuer = public
    provider.claims = {"iss": public}
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        config = OidcConfig(
            f"{ISSUER}/.well-known/openid-configuration",
            CLIENT,
            "s",
            "openid",
            MAPPING,
            expected_issuer=public,
        )
        _, principal = await login(OidcIdentityProvider(config, http), provider)
    assert principal.groups == {"demo"}


async def test_an_organisation_provider_keeps_its_stored_issuer_when_discovery_changes(
    provider: FakeProvider,
) -> None:
    provider.metadata_issuer = "https://elsewhere.example"
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
        adapter = OidcIdentityProvider(_organisation_config(expected_issuer=ISSUER), http)
        with pytest.raises(IdentityError, match="discovery_failed"):
            await adapter.start_login(REDIRECT)


async def test_a_token_with_another_iss_than_the_pinned_one_is_refused(
    oidc: OidcIdentityProvider, provider: FakeProvider
) -> None:
    provider.claims = {"iss": "https://evil.example"}
    with pytest.raises(IdentityError, match="invalid_token"):
        await login(oidc, provider)


@pytest.mark.parametrize(
    ("client_id", "secret"),
    [("client:dept", "s+%"), ("plain", "a b"), ("é", "p:w")],
)
def test_client_secret_basic_encodes_each_credential_before_joining(
    client_id: str, secret: str
) -> None:
    scheme, encoded = client_secret_basic(client_id, secret).split(" ")

    raw_id, raw_secret = base64.b64decode(encoded).decode().split(":")
    assert scheme == "Basic"
    assert (unquote_plus(raw_id), unquote_plus(raw_secret)) == (client_id, secret)
