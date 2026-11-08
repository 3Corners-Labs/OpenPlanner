"""The walk over a route's dependency tree (``app/web/route_dependencies.py``)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from app.web.route_dependencies import dependency_calls


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
