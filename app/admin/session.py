"""The admin session: who is logged in, and the OIDC login in progress (ADR 0013).

Stored in Starlette's signed session cookie (``op_admin``, path ``/admin``). Holds the
principal's subject, display name, roles, organisations and groups, the other role claim
values (custom roles link to them, ADR 0039; the roles themselves are looked up per request),
the login time, when activity was last written to the staff record (ADR 0046), the chosen
theme, the alert preferences and the scope filter. The absolute lifetime is enforced here; the
cookie's own expiry slides with every response. The theme, the preferences and the filter live
here so that they need no cookie of their own.
"""

from dataclasses import dataclass
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
_TRIAL = "login_trial"
_THEME = "theme"
_ALERT_BANNER = "alert_banner"
_ALERT_POPUPS = "alert_popups"
_SCOPE = "scope"

THEMES = ("system", "light", "dark")
"""The portal's colour themes; ``system`` follows the device setting and is the default."""

ALERT_POPUPS = ("all", "warnings", "outages", "off")
"""Which alerts may pop up (ADR 0061 §7): all, warnings and outages, outages only, none."""

DEFAULT_ALERT_POPUPS = "warnings"
"""The pop-up choice until the admin picks one."""


@dataclass(frozen=True)
class AlertPrefs:
    """What the admin chose for alerts in the profile menu (stored in the session for now).

    Attributes:
        banner: Whether the outage banner shows (ADR 0061 §7).
        popups: One of ``ALERT_POPUPS``.
    """

    banner: bool
    popups: str


def save_login_start(request: Request, start: LoginStart, next_url: str) -> None:
    """Remember the secrets of a login in progress and where to go afterwards."""
    request.session[_OIDC] = {
        "state": start.state,
        "nonce": start.nonce,
        "verifier": start.code_verifier,
        "next": next_url,
    }


def save_login_trial(request: Request, subject: str, values: dict[str, str], now: datetime) -> None:
    """Remember a proposed login mapping until the dry run of the next login (ADR 0042 §8).

    The session cookie is signed; the values are claim names and role values, not secrets.
    """
    request.session[_TRIAL] = {"subject": subject, "values": values, "at": now.isoformat()}


def has_login_trial(request: Request) -> bool:
    """Return whether a proposed login mapping waits for its dry run."""
    return isinstance(request.session.get(_TRIAL), dict)


def pop_login_trial(
    request: Request, now: datetime, max_age: timedelta
) -> tuple[str, dict[str, str]] | None:
    """Return and forget the proposed mapping (each one is tried once).

    A proposal older than ``max_age`` (the re-login limit) is dropped: an abandoned dry run
    must not be completed by a login days later.
    """
    value = request.session.pop(_TRIAL, None)
    if not isinstance(value, dict) or not isinstance(value.get("values"), dict):
        return None
    try:
        made = datetime.fromisoformat(str(value.get("at", "")))
    except ValueError:
        return None
    if made.tzinfo is None or now - made > max_age:
        return None
    values = {str(k): str(v) for k, v in value["values"].items()}
    return str(value.get("subject", "")), values


def pop_login_start(request: Request) -> dict[str, str] | None:
    """Return and forget the login in progress (each one can be completed once)."""
    value = request.session.pop(_OIDC, None)
    return value if isinstance(value, dict) else None


def start_session(request: Request, principal: Principal, now: datetime) -> None:
    """Replace the whole session with a fresh one for ``principal`` (no session fixation).

    Only the theme and the alert preferences carry over, so a fresh login (ADR 0015) keeps the
    admin's choices.
    """
    theme = session_theme(request)
    prefs = session_alert_prefs(request)
    request.session.clear()
    if theme != THEMES[0]:
        request.session[_THEME] = theme
    if prefs != AlertPrefs(True, DEFAULT_ALERT_POPUPS):
        request.session[_ALERT_BANNER] = prefs.banner
        request.session[_ALERT_POPUPS] = prefs.popups
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


def session_theme(request: Request) -> str:
    """Return the theme chosen in this session, or ``system``."""
    value: Any = request.session.get(_THEME)
    return value if value in THEMES else THEMES[0]


def choose_theme(request: Request, theme: str | None) -> str:
    """Remember ``theme`` for this session if it is a known theme; return the session's theme."""
    if theme in THEMES:
        request.session[_THEME] = theme
    return session_theme(request)


def session_alert_prefs(request: Request) -> AlertPrefs:
    """Return the alert preferences of this session: the banner on and ``warnings`` by default."""
    banner: Any = request.session.get(_ALERT_BANNER)
    popups: Any = request.session.get(_ALERT_POPUPS)
    return AlertPrefs(
        banner if isinstance(banner, bool) else True,
        popups if popups in ALERT_POPUPS else DEFAULT_ALERT_POPUPS,
    )


def choose_alert_prefs(
    request: Request, *, sent: bool, banner: bool, popups: str | None
) -> AlertPrefs:
    """Remember the alert preferences of a submitted form; return the session's preferences.

    A ticked box sends a value and an unticked one sends nothing, so ``banner`` only counts when
    the form was ``sent`` (its marker field arrived). An unknown ``popups`` value is ignored.
    """
    if sent:
        request.session[_ALERT_BANNER] = banner
        if popups in ALERT_POPUPS:
            request.session[_ALERT_POPUPS] = popups
    return session_alert_prefs(request)


def session_scope(request: Request) -> tuple[str | None, str | None]:
    """Return the organisation and group slug the admin chose in the scope filter, or ``None``.

    The values are only what the admin last submitted; the caller checks them against the
    admin's own scope on every request (``app.admin.scope_choice``) and never trusts them.
    """
    value: Any = request.session.get(_SCOPE)
    if not isinstance(value, list) or len(value) != 2:
        return None, None
    organisation, group = value
    return (
        organisation if isinstance(organisation, str) else None,
        group if isinstance(group, str) else None,
    )


def remember_scope(request: Request, organisation: str | None, group: str | None) -> None:
    """Keep the (already checked) scope choice for this session; both ``None`` forgets it."""
    if organisation is None and group is None:
        request.session.pop(_SCOPE, None)
    else:
        request.session[_SCOPE] = [organisation, group]
