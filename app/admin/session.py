"""The admin session: a reference in the cookie, the session itself on the server (ADR 0098).

The signed cookie (``__Host-op_admin`` on https, ``op_admin`` over http; see
``session_cookie_for``) holds ``sid``, a random reference, next to what exists before
a session does: the OIDC login in progress, the test login, the update snooze, and the chosen
theme, menu width, alert preferences and scope filter. Who is logged in (``LoginClaims``:
subject, display name, roles, organisations, groups, entitlements), when, and until when lives
in the register (``app.domain.admin_session_service``) and is looked up on every request, so
logging out, a new login, the idle limit and the absolute lifetime all take effect on the
server, whatever copy of the cookie someone holds. The grants themselves are resolved from the
claims on every request (``app.domain.login_service``), never stored.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request

from app.domain.admin_session_service import AdminSessionService
from app.domain.admin_sessions import AdminSession, EndReason, SessionPolicy
from app.domain.entitlements import LoginClaims
from app.domain.identity import PLATFORM
from app.domain.ports import LoginStart
from app.web.cookies import CookieSpec, cookie_for

_BASE_COOKIE = "op_admin"
_SID = "sid"
_OIDC = "oidc"
_TRIAL = "login_trial"
_THEME = "theme"
_NAV = "nav"
_ALERT_BANNER = "alert_banner"
_ALERT_POPUPS = "alert_popups"
_SCOPE = "scope"

THEMES = ("system", "light", "dark")
"""The portal's colour themes; ``system`` follows the device setting and is the default."""

NAV_CHOICES = ("collapsed", "expanded")
"""How wide the admin wants the side navigation: the icon rail or the full menu.

Without a choice the width decides (the rail on a tablet, the full menu on a desktop); the
choice overrides it on both, as the admin shell design does.
"""

ALERT_POPUPS = ("all", "warnings", "outages", "off")
"""Which alerts may pop up (ADR 0061 §7): all, warnings and outages, outages only, none."""

DEFAULT_ALERT_POPUPS = "warnings"
"""The pop-up choice until the admin picks one."""


SessionCookie = CookieSpec
"""The name and attributes of the admin session cookie (ADR 0098 point 12)."""


def session_cookie_for(public_base_url: str) -> SessionCookie:
    """Return the admin cookie to use for the address the installation is served on.

    ``__Host-op_admin`` with ``Path=/`` and ``Secure`` on an ``https://`` address, ``op_admin``
    with ``Path=/admin`` over ``http://`` (development). The rule is shared with the
    participant's cookie in ``app.web.cookies``; the two cookies are independent.

    Args:
        public_base_url: The ``PUBLIC_BASE_URL`` setting.

    Returns:
        The cookie name, path and ``Secure`` flag for ``SessionMiddleware``.
    """
    return cookie_for(public_base_url, _BASE_COOKIE, "/admin")


@dataclass(frozen=True)
class AlertPrefs:
    """What the admin chose for alerts in the profile menu (stored in the session for now).

    Attributes:
        banner: Whether the outage banner shows (ADR 0061 §7).
        popups: One of ``ALERT_POPUPS``.
    """

    banner: bool
    popups: str


def save_login_start(
    request: Request, start: LoginStart, next_url: str, provider: str = PLATFORM
) -> None:
    """Remember the secrets of a login in progress, its provider and where to go afterwards.

    The callback completes the login only at ``provider`` (ADR 0083 point 14), so the answer
    of one provider can never finish a login started at another.
    """
    request.session[_OIDC] = {
        "state": start.state,
        "nonce": start.nonce,
        "verifier": start.code_verifier,
        "next": next_url,
        "provider": provider,
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


_TEST = "login_test"


def save_login_test(
    request: Request, subject: str, organisation: str, provider: str, now: datetime
) -> None:
    """Remember that the superuser started a guided test login (ADR 0083 point 9).

    The callback shows the report to the admin who started it and starts no session; the
    test belongs to ``subject`` and to ``provider``, so a login that comes back for another
    provider or in another admin's session is refused.
    """
    request.session[_TEST] = {
        "subject": subject,
        "organisation": organisation,
        "provider": provider,
        "at": now.isoformat(),
    }


def pop_login_test(
    request: Request, now: datetime, max_age: timedelta
) -> tuple[str, str, str] | None:
    """Return and forget the pending test login: subject, organisation and provider key.

    A test older than ``max_age`` (the re-login limit) is dropped, like a login trial.
    """
    value = request.session.pop(_TEST, None)
    if not isinstance(value, dict):
        return None
    try:
        made = datetime.fromisoformat(str(value.get("at", "")))
    except ValueError:
        return None
    if made.tzinfo is None or now - made > max_age:
        return None
    return (
        str(value.get("subject", "")),
        str(value.get("organisation", "")),
        str(value.get("provider", "")),
    )


def pop_login_start(request: Request) -> dict[str, str] | None:
    """Return and forget the login in progress (each one can be completed once)."""
    value = request.session.pop(_OIDC, None)
    return value if isinstance(value, dict) else None


async def start_session(
    request: Request,
    service: AdminSessionService,
    claims: LoginClaims,
    provider: str,
    policy: SessionPolicy,
) -> None:
    """Replace the whole session with a fresh one for ``claims`` (no session fixation).

    The session the browser brought is ended (``replaced``), so a copy of the old cookie is
    dead after this login (ADR 0098 point 6). Only the theme, the menu width and the alert
    preferences carry over, so a fresh login (ADR 0015) keeps the admin's choices. The new
    cookie holds the new reference and nothing about the person.
    """
    old = _sid(request)
    theme = session_theme(request)
    nav = session_nav(request)
    prefs = session_alert_prefs(request)
    request.session.clear()
    request.state.admin_login = None
    if theme != THEMES[0]:
        request.session[_THEME] = theme
    if nav is not None:
        request.session[_NAV] = nav
    if prefs != AlertPrefs(True, DEFAULT_ALERT_POPUPS):
        request.session[_ALERT_BANNER] = prefs.banner
        request.session[_ALERT_POPUPS] = prefs.popups
    request.session[_SID] = await service.start(claims, provider, policy, replaces=old)


def _sid(request: Request) -> str | None:
    """Return the session reference of the cookie, if it holds a plausible one."""
    value: Any = request.session.get(_SID)
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


async def load_login(request: Request, service: AdminSessionService, policy: SessionPolicy) -> bool:
    """Look the session up once for this request; return whether activity was just written.

    Sets ``request.state.admin_login`` to the live session or ``None``. A reference that no
    longer opens a session is dropped from the cookie. A failing register raises, so there is
    no access (ADR 0098 point 14).
    """
    found, written = await service.resolve(_sid(request), policy)
    request.state.admin_login = found
    if found is None:
        request.session.pop(_SID, None)
    return written


def current_session(request: Request) -> AdminSession | None:
    """Return the live session of this request, or ``None`` before ``load_login`` or without."""
    found: AdminSession | None = getattr(request.state, "admin_login", None)
    return found


def current_login(request: Request) -> LoginClaims | None:
    """Return the logged-in admin's claims, or ``None`` (not logged in, ended or expired)."""
    found = current_session(request)
    return found.claims if found is not None else None


def login_age(request: Request, now: datetime) -> timedelta | None:
    """Return how long ago the admin logged in, or ``None`` without a session."""
    found = current_session(request)
    return now - found.login_at if found is not None else None


async def end_session(
    request: Request, service: AdminSessionService, reason: EndReason = EndReason.LOGOUT
) -> None:
    """End the session on the server first, then forget everything in the cookie."""
    sid = _sid(request)
    request.state.admin_login = None
    try:
        await service.end(sid, reason)
    finally:
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


def session_nav(request: Request) -> str | None:
    """Return the menu width chosen in this session (one of ``NAV_CHOICES``), or ``None``."""
    value: Any = request.session.get(_NAV)
    return value if value in NAV_CHOICES else None


def choose_nav(request: Request, nav: str | None) -> str | None:
    """Remember ``nav`` for this session if it is a known choice; return the session's choice."""
    if nav in NAV_CHOICES:
        request.session[_NAV] = nav
    return session_nav(request)


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
