"""Route inspection for the coverage tests of ``RequirePermission`` and ``RequireScope``."""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute


def all_routes(routes: list[object]) -> list[APIRoute]:
    """Flatten an app's routes, including routers mounted with include_router."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        inner = getattr(route, "original_router", None)  # FastAPI's included-router wrapper
        if inner is not None:
            found.extend(all_routes(inner.routes))
    return found


SCOPE_PARAMETERS = frozenset(
    {"slug", "org", "organisation", "organisation_id", "group", "group_id"}
)
"""Parameter names that address an organisation or a group, in a path or a query."""


def parameter_names(dependant: Dependant) -> set[str]:
    """Return every path and query parameter of a route, through its dependencies."""
    names = {p.name for p in (*dependant.path_params, *dependant.query_params)}
    for inner in dependant.dependencies:
        names |= parameter_names(inner)
    return names


def scoped_routes(routes: list[APIRoute]) -> dict[str, APIRoute]:
    """Return the routes that address an organisation or a group, keyed ``METHOD path``."""
    return {
        f"{method} {route.path}": route
        for route in routes
        if parameter_names(route.dependant) & SCOPE_PARAMETERS
        for method in sorted(route.methods)
    }
