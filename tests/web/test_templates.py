"""The language switcher's links: kept parameters only, and only values shaped like dates.

On a page served at a ``GET`` action's address the links lead to the overview instead.
"""

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.web.protection import require_same_origin
from app.web.templates import switch_href


def _request(query: str) -> Request:
    return Request({"type": "http", "query_string": query.encode(), "headers": []})


def _route(*, action: bool) -> APIRoute:
    """A route that depends on ``require_same_origin`` (an action) or on nothing."""
    router = APIRouter()
    if action:

        @router.get("/x")
        def guarded(_: Annotated[None, Depends(require_same_origin)]) -> None:
            """An action."""

    else:

        @router.get("/x")
        def page() -> None:
            """A page."""

    (route,) = router.routes
    assert isinstance(route, APIRoute)
    return route


def _at(method: str, *, action: bool) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "query_string": b"day=2026-01-02",
        "headers": [],
        "route": _route(action=action),
    }
    return Request(scope)


@pytest.mark.parametrize(
    ("query", "href"),
    [
        ("day=2026-01-02&month=2026-01", "?day=2026-01-02&month=2026-01&lang=en"),
        ("start=2026-01-02T08:00:00Z&days=7", "?start=2026-01-02T08%3A00%3A00Z&days=7&lang=en"),
        (
            "start=2026-01-02T08%3A00%3A00%2B00%3A00",
            "?start=2026-01-02T08%3A00%3A00%2B00%3A00&lang=en",
        ),
        ("evil=1&next=//evil.example", "?lang=en"),
    ],
)
def test_the_switcher_keeps_known_parameters_with_date_shaped_values(query: str, href: str) -> None:
    assert switch_href(_request(query), "lang", "en") == href


@pytest.mark.parametrize(
    "value",
    ["zqmail7x@example.org", "Ada Lovelace", "", "-1", "2026-01-02%22onmouseover", "x" * 41],
)
def test_a_kept_parameter_with_text_in_it_is_dropped_not_echoed(value: str) -> None:
    """ADR 0063 §3: a personal-data value typed into a list URL never comes back in a link."""
    assert switch_href(_request(f"day={value}&days={value}"), "lang", "nl") == "?lang=nl"


def test_on_a_get_actions_page_the_switcher_leads_to_the_overview() -> None:
    # The refusal of an audited download is served at the download's address: a query-only
    # link there would ask for the download again (ASVS V3.5.3).
    assert switch_href(_at("GET", action=True), "lang", "en") == "/admin/?lang=en"


@pytest.mark.parametrize(("method", "action"), [("POST", True), ("GET", False)])
def test_a_form_post_or_an_ordinary_page_keeps_its_query_only_links(
    method: str, action: bool
) -> None:
    assert switch_href(_at(method, action=action), "lang", "en") == "?day=2026-01-02&lang=en"
