"""Where an admin comes back to after a login, a re-login or a "return to" form.

One home for the concern: ``safe_next`` checks every target that arrives with a request (the
``next`` of the login, the re-login, the update banner's snooze, the preferences and the alert
banner), and ``return_path`` names the current page as such a target (for a form post or a
download, the page that asked for it, so an action is never replayed). A target is always a
path under ``/admin`` on this site. Its query keeps only the view parameters of
``RETURN_QUERY_PARAMS`` (an allow-list), so a token, a confirmation, a typed search or any
other personal data never travels into a login URL or the session cookie.
"""

import re
from urllib.parse import parse_qsl, urlencode

from fastapi import Request

from app.web.protection import require_same_origin
from app.web.route_dependencies import dependency_calls
from app.web.templates import KEPT_QUERY_PARAMS

DASHBOARD = "/admin/"
"""Where a target that fails the check goes instead."""

RETURN_QUERY_PARAMS = frozenset(
    {
        *KEPT_QUERY_PARAMS,
        "view",
        "level",
        "archived",
        "closures",
        "group",
        "org",
        "year",
        "weekday",
        "to",
        "page",
    }
)
"""Fixed rule, no setting: the query parameters that select a view and may come back.

Dates (``KEPT_QUERY_PARAMS``), tabs, filters on a closed list and slugs. Never ``q`` (typed
text), ``done``/``count`` (a confirmation shown once), nor any token or code.
"""

RETURN_QUERY_MAX_PARAMS = 8
"""Fixed rule, no setting: a query with more parameters than this comes back not at all."""

_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
"""The shape of a kept value: a name from a closed list, a slug, a date or a number."""

_ANCHOR = re.compile(r"[A-Za-z0-9_-]{1,64}")
"""The shape of a kept anchor, such as ``widget-alerts``."""


def _kept_query(query: str) -> str:
    """Return the view parameters of ``query``, encoded again, or ``""``."""
    pairs = parse_qsl(query, keep_blank_values=True)
    if len(pairs) > RETURN_QUERY_MAX_PARAMS:
        return ""
    return urlencode([(k, v) for k, v in pairs if k in RETURN_QUERY_PARAMS and _VALUE.fullmatch(v)])


def safe_next(value: str | None) -> str:
    """Return ``value`` if it is an admin path on this site; otherwise the dashboard.

    The path must start with ``/admin`` and contain no ``//`` at the start, no backslash and
    no ``://`` anywhere, checked on the whole value before anything is split. Its query keeps
    only the view parameters of ``RETURN_QUERY_PARAMS``, and an anchor only when it is a
    plain name.
    """
    if not (
        value
        and value.startswith("/admin")
        and not value.startswith("//")
        and "\\" not in value
        and "://" not in value
    ):
        return DASHBOARD
    rest, _, fragment = value.partition("#")
    path, _, query = rest.partition("?")
    kept = _kept_query(query)
    anchor = f"#{fragment}" if _ANCHOR.fullmatch(fragment) else ""
    return f"{path}?{kept}{anchor}" if kept else f"{path}{anchor}"


def _is_action(request: Request) -> bool:
    """Return whether the request's route is an action: it depends on ``require_same_origin``.

    Every form post is, and so is a GET with an effect, such as an audited download
    (ASVS V3.5.3).
    """
    return require_same_origin in dependency_calls(request.scope.get("route"))


def _page(request: Request) -> tuple[str, str]:
    """Return the path and raw query of the page the admin is on.

    A GET is that page, unless it is an action. A POST and a GET action return to the page
    that sent them (the referer), so a login or a re-login never replays an action: the
    identity provider's redirect back to it would arrive cross-site and be refused. A forged
    referer cannot leave the portal, because every target goes through ``safe_next``.
    """
    if request.method == "GET" and not _is_action(request):
        return request.url.path, request.url.query
    referer = request.headers.get("referer", "")
    rest = referer.split("://", 1)[-1].partition("/")[2].split("#", 1)[0]
    path, _, query = rest.partition("?")
    return (f"/{path}", query) if rest else (DASHBOARD, "")


def page_path(request: Request) -> str:
    """Return the path of the page the admin is on, without its query."""
    return _page(request)[0]


def return_path(request: Request) -> str:
    """Return the page to come back to after a login or a re-login: path and view query.

    The query is filtered before the check, so a value that names another site drops out
    and the page itself still comes back.
    """
    path, query = _page(request)
    kept = _kept_query(query)
    return safe_next(f"{path}?{kept}" if kept else path)
