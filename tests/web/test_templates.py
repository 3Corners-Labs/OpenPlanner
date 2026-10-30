"""The language switcher's links: kept parameters only, and only values shaped like dates."""

import pytest
from starlette.requests import Request

from app.web.templates import switch_href


def _request(query: str) -> Request:
    return Request({"type": "http", "query_string": query.encode(), "headers": []})


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
