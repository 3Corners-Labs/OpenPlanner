"""The admin session: who is logged in, and the OIDC login in progress (ADR 0013).

Stored in Starlette's signed session cookie (``op_admin``, path ``/admin``). Holds the
principal's subject, display name, roles, organisations and groups, the other role claim
values (custom roles link to them, ADR 0039; the roles themselves are looked up per request),
the login time and when activity was last written to the staff record (ADR 0046). The
absolute lifetime is enforced here; the cookie's own expiry slides with every response.
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import Request

from app.domain.authorization import Principal, Role
from app.domain.ports import LoginStart
from app.domain.roles import MAX_ROLE_CLAIMS, is_claim_value

_PRINCIPAL = "principal"
_LOGIN_AT = "login_at"
_SEEN_AT = "seen_at"
_OIDC = "oidc"


def save_login_start(request: Request, start: LoginStart, next_url: str) -> None:
    """Remember the secrets of a login in progress and where to go afterwards."""
    request.session[_OIDC] = {
        "state": start.state,
        "nonce": start.nonce,
        "verifier": start.code_verifier,
        "next": next_url,
    }


def pop_login_start(request: Request) -> dict[str, str] | None:
    """Return and forget the login in progress (each one can be completed once)."""
    value = request.session.pop(_OIDC, None)
    return value if isinstance(value, dict) else None


def start_session(request: Request, principal: Principal, now: datetime) -> None:
    """Replace the whole session with a fresh one for ``principal`` (no session fixation)."""
    request.session.clear()
    request.session[_PRINCIPAL] = {
        "subject": principal.subject,
        "name": principal.display_name,
        "roles": sorted(principal.roles),
        "organisations": sorted(principal.organisations),
        "groups": sorted(principal.groups),
        "claims": sorted(principal.role_claims),
    }
    request.session[_LOGIN_AT] = now.isoformat()
    request.session[_SEEN_AT] = now.isoformat()


def current_principal(request: Request, now: datetime, lifetime: timedelta) -> Principal | None:
    """Return the logged-in principal, or ``None`` (not logged in, expired or tampered)."""
    data: Any = request.session.get(_PRINCIPAL)
    login_at: Any = request.session.get(_LOGIN_AT)
    if not isinstance(data, dict) or not isinstance(login_at, str):
        return None
    try:
        started = datetime.fromisoformat(login_at)
        principal = Principal(
            subject=str(data["subject"]),
            display_name=str(data["name"]),
            roles=frozenset(Role(r) for r in data["roles"]),
            organisations=frozenset(str(o) for o in data["organisations"]),
            groups=frozenset(str(g) for g in data["groups"]),
            role_claims=frozenset(
                str(c) for c in data.get("claims", [])[:MAX_ROLE_CLAIMS] if is_claim_value(str(c))
            ),
        )
    except (KeyError, TypeError, ValueError):
        request.session.clear()
        return None
    if now - started > lifetime:
        request.session.clear()
        return None
    return principal


def login_age(request: Request, now: datetime) -> timedelta | None:
    """Return how long ago the admin logged in, or ``None`` without a (valid) login time."""
    login_at: Any = request.session.get(_LOGIN_AT)
    if not isinstance(login_at, str):
        return None
    try:
        return now - datetime.fromisoformat(login_at)
    except ValueError:
        return None


def activity_due(request: Request, now: datetime, interval: timedelta) -> bool:
    """Return whether activity is due to be written, and if so remember ``now`` as written.

    Keeps the write to the staff record at most once per ``interval`` without a database
    read on every request.
    """
    seen_at: Any = request.session.get(_SEEN_AT)
    try:
        due = not isinstance(seen_at, str) or now - datetime.fromisoformat(seen_at) >= interval
    except ValueError:
        due = True
    if due:
        request.session[_SEEN_AT] = now.isoformat()
    return due


def end_session(request: Request) -> None:
    """Forget everything about the admin."""
    request.session.clear()
