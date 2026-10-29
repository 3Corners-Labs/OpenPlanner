"""Disabled buttons during maintenance, decided from the same matrix as the server (ADR 0038).

While maintenance is on, an admin page shows every change that would be refused with its
submit buttons disabled and described by the maintenance bar. The page is not edited per
template: ``freeze_forms`` reads the ``action`` of each POST form, finds the admin route that
would answer it, and asks ``decide_in_maintenance`` with that route's ``RequirePermission``.
A form whose action matches no admin route is frozen too: the safe default.

    rendered page ─► <form method="post" action=X> ─► route of X ─► RequirePermission
                     ─► decide_in_maintenance(principal, permission, maintenance_safe)
                     ─► refused? <button type="submit" disabled aria-describedby=...>

The server decides again on every request; this only keeps admins from filling in a form
that cannot be saved.
"""

import html
import re
from collections.abc import Callable, Iterable

from fastapi.routing import APIRoute
from starlette.routing import Match

from app.admin.authz import RequirePermission
from app.domain.authorization import Principal, decide_in_maintenance

BAR_ID = "maintenance-bar"
"""The id of the maintenance bar (``_maintenance.html``), which says why buttons are disabled."""

_FORM = re.compile(r"<form\b[^>]*>.*?</form>", re.DOTALL | re.IGNORECASE)
_POST = re.compile(r'\bmethod="post"', re.IGNORECASE)
_ACTION = re.compile(r'\baction="([^"]*)"', re.IGNORECASE)
_SUBMIT = re.compile(r'<button type="submit"')
_FROZEN = f'<button type="submit" disabled aria-describedby="{BAR_ID}"'


def _routes(routes: Iterable[object]) -> list[APIRoute]:
    """Flatten an app's routes, including routers mounted with ``include_router``."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        inner = getattr(route, "original_router", None)
        if inner is not None:
            found.extend(_routes(inner.routes))
    return found


def requirement_for(routes: Iterable[object], path: str) -> RequirePermission | None:
    """Return the ``RequirePermission`` of the admin route that answers a POST to ``path``."""
    scope = {"type": "http", "path": path, "method": "POST", "root_path": ""}
    for route in _routes(routes):
        match, _ = route.matches(scope)
        if match is not Match.FULL:
            continue
        stack = list(route.dependant.dependencies)
        while stack:
            dependency = stack.pop()
            if isinstance(dependency.call, RequirePermission):
                return dependency.call
            stack.extend(dependency.dependencies)
        return None
    return None


def writable_in_maintenance(
    routes: Iterable[object], principal: Principal
) -> Callable[[str], bool]:
    """Return a check: may ``principal`` still submit a form to this action during maintenance?"""
    known = list(routes)

    def writable(action: str) -> bool:
        path = html.unescape(action).split("?", 1)[0].split("#", 1)[0]
        requirement = requirement_for(known, path)
        if requirement is None:
            return False
        return decide_in_maintenance(
            principal, requirement.permission, maintenance_safe=requirement.maintenance_safe
        ).allowed

    return writable


def freeze_forms(page: str, writable: Callable[[str], bool]) -> str:
    """Disable the submit buttons of every POST form whose action is not ``writable``.

    The logout form is a POST too; it has no ``RequirePermission`` and is left alone.
    """

    def freeze(found: re.Match[str]) -> str:
        form = found.group(0)
        opening = form[: form.index(">") + 1]
        action = _ACTION.search(opening)
        if not _POST.search(opening) or action is None:
            return form
        if action.group(1) in LEFT_ALONE or writable(action.group(1)):
            return form
        return _SUBMIT.sub(_FROZEN, form)

    return _FORM.sub(freeze, page)


LEFT_ALONE = frozenset({"/admin/logout"})
"""POST forms outside the matrix that maintenance never touches (ending the own session)."""
