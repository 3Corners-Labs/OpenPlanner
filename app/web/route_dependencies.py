"""What a route depends on: the app's routes, and the walk over a route's dependency tree.

For code that decides from the guards and parameters a route declares rather than from its
path: which permission a form's action needs while maintenance is on
(``app.admin.frozen_forms``), whether a request's route is an action
(``app.web.protection.is_action``), and the tests that every route declares exactly one
permission or scope and names no personal-data field in its query.
"""

from collections.abc import Iterable

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute


def all_routes(routes: Iterable[object]) -> list[APIRoute]:
    """Flatten an app's routes, including routers mounted with ``include_router``."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        inner = getattr(route, "original_router", None)  # FastAPI's included-router wrapper
        if inner is not None:
            found.extend(all_routes(inner.routes))
    return found


def dependants(dependant: Dependant) -> list[Dependant]:
    """Return ``dependant`` and every dependency below it, nested ones included.

    Each ``Depends(...)`` appears once per place it is declared; the route's own comes first.
    """
    found: list[Dependant] = []
    stack = [dependant]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(current.dependencies)
    return found


def dependency_calls(route: object) -> list[object]:
    """Return every callable ``route`` depends on, nested dependencies included.

    Args:
        route: A route of the application, such as the matched ``request.scope["route"]``.
            Anything without a FastAPI dependency tree (``None`` for an unmatched request)
            depends on nothing.

    Returns:
        The dependency callables, each ``Depends(...)`` target once per place it is declared.
    """
    dependant = getattr(route, "dependant", None)
    if not isinstance(dependant, Dependant):
        return []
    return [inner.call for inner in dependants(dependant)[1:]]


def guards[G](route: object, kind: type[G]) -> list[G]:
    """Return the dependencies of ``route`` that are instances of ``kind``.

    Args:
        route: As for ``dependency_calls``.
        kind: A guard class, such as ``RequirePermission`` or ``RequireScope``.
    """
    return [call for call in dependency_calls(route) if isinstance(call, kind)]
