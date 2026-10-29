"""``oidc-check``: check the admin login configuration against the provider, without a login.

ADR 0042 §8 rule 4 and §10: when nobody can log in, the operator needs to know whether the
provider, its keys or our configuration is at fault, from the host and without the portal.
Only two reads are made (the discovery document and the key set); no login is started and no
token is requested. The report names settings and states ``set``/``not set``; it never prints
a secret or a key.

    GET discovery ─► algorithms, PKCE ─► GET jwks_uri ─► usable asymmetric keys
    local: redirect URL (https in production), client secret set
"""

from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.adapters.diagnostics import CheckResult
from app.adapters.oidc import ALLOWED_ALGORITHMS

REQUIRED_METADATA = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")


@dataclass(frozen=True)
class OidcTarget:
    """What ``oidc-check`` compares the provider with.

    Attributes:
        discovery_url: ``OIDC_DISCOVERY_URL``.
        redirect_uri: The callback the provider must have registered.
        require_https: Production: the redirect URL must be https and a secret is required.
        client_secret_set: Whether ``OIDC_CLIENT_SECRET`` is set (never its value).
    """

    discovery_url: str
    redirect_uri: str
    require_https: bool
    client_secret_set: bool


async def check_oidc(http: httpx.AsyncClient, target: OidcTarget) -> list[CheckResult]:
    """Return the findings; stop after the discovery when the document is unusable."""
    results = _local_checks(target)
    try:
        response = await http.get(target.discovery_url)
    except httpx.HTTPError as exc:
        return [CheckResult("discovery", None, False, type(exc).__name__), *results]
    metadata = _metadata(response)
    if metadata is None:
        hint = f"no usable discovery document ({', '.join(REQUIRED_METADATA)} required)"
        return [CheckResult("discovery", response.status_code, False, hint), *results]
    return [
        CheckResult("discovery", response.status_code, True, f"issuer {metadata['issuer']}"),
        _algorithms(metadata),
        _pkce(metadata),
        await _signing_keys(http, str(metadata["jwks_uri"])),
        *results,
    ]


def _metadata(response: httpx.Response) -> dict[str, Any] | None:
    """Return the discovery document if it is a JSON object with the required endpoints."""
    if response.status_code != httpx.codes.OK:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or not all(
        isinstance(body.get(key), str) for key in REQUIRED_METADATA
    ):
        return None
    return body


def _algorithms(metadata: dict[str, Any]) -> CheckResult:
    """Check that the provider can sign ID tokens with an algorithm we accept."""
    offered = metadata.get("id_token_signing_alg_values_supported")
    if not isinstance(offered, list):
        return CheckResult(
            "signing algorithms", None, False, "not advertised; check by logging in", False
        )
    names = [str(alg) for alg in offered]
    usable = [alg for alg in names if alg in ALLOWED_ALGORITHMS]
    if usable:
        return CheckResult("signing algorithms", None, True, ", ".join(usable))
    hint = f"offers {', '.join(names) or 'none'}; OpenPlanner accepts only asymmetric ones"
    return CheckResult("signing algorithms", None, False, hint)


def _pkce(metadata: dict[str, Any]) -> CheckResult:
    """Check that S256 is advertised (a warning only: many providers do not advertise it)."""
    methods = metadata.get("code_challenge_methods_supported")
    ok = isinstance(methods, list) and "S256" in methods
    hint = "advertised" if ok else "not advertised; login fails if the provider refuses it"
    return CheckResult("PKCE S256", None, ok, hint, required=False)


async def _signing_keys(http: httpx.AsyncClient, jwks_uri: str) -> CheckResult:
    """Count the provider's keys that we could verify an ID token with; never print a key."""
    try:
        response = await http.get(jwks_uri)
        response.raise_for_status()
        body = response.json()
        keys = body.get("keys") if isinstance(body, dict) else None
    except (httpx.HTTPError, ValueError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        return CheckResult(
            "signing keys", status, False, f"key set unreadable ({type(exc).__name__})"
        )
    usable = sum(1 for key in keys if _usable(key)) if isinstance(keys, list) else 0
    if usable:
        return CheckResult("signing keys", response.status_code, True, f"{usable} usable")
    return CheckResult(
        "signing keys", response.status_code, False, "no usable asymmetric signing key"
    )


def _usable(key: object) -> bool:
    """Whether a JWK is a well-formed asymmetric key that may sign (``use`` absent or sig)."""
    if not isinstance(key, dict) or key.get("kty") not in {"RSA", "EC"}:
        return False
    if key.get("use", "sig") != "sig" or key.get("alg", "RS256") not in ALLOWED_ALGORITHMS:
        return False
    try:
        jwt.PyJWK.from_dict(key)
    except jwt.PyJWTError:
        return False
    return True


def _local_checks(target: OidcTarget) -> list[CheckResult]:
    """Check what needs no network: the redirect URL and whether a secret is set."""
    https = target.redirect_uri.startswith("https://")
    redirect = CheckResult(
        "redirect URL",
        None,
        https or not target.require_https,
        target.redirect_uri + ("" if https else " (must be https in production)"),
    )
    secret = CheckResult(
        "client secret",
        None,
        target.client_secret_set or not target.require_https,
        "set" if target.client_secret_set else "not set",
    )
    return [redirect, secret]
