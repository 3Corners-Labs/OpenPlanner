"""Route inspection for the coverage tests of ``RequirePermission`` and ``RequireScope``."""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.web.route_dependencies import all_routes, dependants

__all__ = ["SCOPE_PARAMETERS", "all_routes", "parameter_names", "scoped_routes"]

SCOPE_PARAMETERS = frozenset(
    {"slug", "org", "organisation", "organisation_id", "group", "group_id"}
)
"""Parameter names that address an organisation or a group, in a path or a query."""


def parameter_names(dependant: Dependant) -> set[str]:
    """Return every path and query parameter of a route, through its dependencies."""
    return {
        parameter.name
        for inner in dependants(dependant)
        for parameter in (*inner.path_params, *inner.query_params)
    }


def scoped_routes(routes: list[APIRoute]) -> dict[str, APIRoute]:
    """Return the routes that address an organisation or a group, keyed ``METHOD path``."""
    return {
        f"{method} {route.path}": route
        for route in routes
        if parameter_names(route.dependant) & SCOPE_PARAMETERS
        for method in sorted(route.methods)
    }
