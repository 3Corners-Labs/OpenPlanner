"""Route inspection for the coverage tests of ``RequirePermission`` and ``RequireScope``."""

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
