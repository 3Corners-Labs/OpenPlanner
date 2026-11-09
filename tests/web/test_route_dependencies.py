"""The app's routes and the walk over their dependency trees (``app/web/route_dependencies.py``)."""

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute

from app.web.route_dependencies import all_routes, dependants, dependency_calls


def _inner() -> None:
    """A dependency of a dependency."""


def _outer(_: Annotated[None, Depends(_inner)]) -> None:
    """A dependency that depends on ``_inner``."""


def _other() -> None:
    """A second dependency of the route."""


def test_every_dependency_is_found_nested_ones_too() -> None:
    router = APIRouter()

    @router.get("/x")
    def handler(_: Annotated[None, Depends(_outer)], __: Annotated[None, Depends(_other)]) -> None:
        """The route under test."""

    (route,) = router.routes
    assert isinstance(route, APIRoute)

    names = sorted(getattr(call, "__name__", "") for call in dependency_calls(route))
    assert names == ["_inner", "_other", "_outer"]


def test_what_is_no_route_depends_on_nothing() -> None:
    assert dependency_calls(None) == []
    assert dependency_calls(object()) == []


def test_the_dependants_start_with_the_route_and_reach_the_nested_ones() -> None:
    router = APIRouter()

    @router.get("/x")
    def handler(_: Annotated[None, Depends(_outer)]) -> None:
        """The route under test."""

    (route,) = router.routes
    assert isinstance(route, APIRoute)

    found = dependants(route.dependant)

    assert found[0] is route.dependant
    assert [getattr(d.call, "__name__", "") for d in found[1:]] == ["_outer", "_inner"]


def test_the_routes_of_an_included_router_are_found() -> None:
    router = APIRouter(prefix="/inner")

    @router.get("/x")
    def handler() -> None:
        """A route of the included router."""

    app = FastAPI()
    app.include_router(router)

    assert "/inner/x" in {route.path for route in all_routes(app.routes)}
