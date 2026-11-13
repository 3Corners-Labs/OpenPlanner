"""One key for every limiter: an IPv6 caller is counted by its /64 (ADR 0124 step 2a)."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.web.caller import caller_of, client_address
from app.web.protection import (
    RateLimits,
    configure_rate_limits,
    count_unknown_link,
    limiter,
    within_webhook_limit,
)


@pytest.fixture(autouse=True)
def _restore_limits() -> Iterator[None]:
    """Leave the process-wide limiter as the other tests expect it."""
    yield
    configure_rate_limits(RateLimits(), enabled=False)


def _request(address: str | None, **headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "app": FastAPI(),
            "path": "/x",
            "query_string": b"",
            "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()],
            "client": None if address is None else (address, 4711),
        }
    )


def test_the_key_is_the_ipv4_address_or_the_ipv6_64() -> None:
    assert caller_of(_request("203.0.113.7")) == "203.0.113.7"
    assert caller_of(_request("2001:db8:1:2::1")) == "2001:db8:1:2::/64"
    assert caller_of(_request("2001:db8:1:2::1")) == caller_of(_request("2001:db8:1:2:f::9"))
    assert caller_of(_request("2001:db8:1:2::1")) != caller_of(_request("2001:db8:1:3::1"))
    assert caller_of(_request("::ffff:203.0.113.7")) == caller_of(_request("203.0.113.7"))
    assert caller_of(_request(None)) == "unknown"


def test_the_address_is_the_one_the_server_derived_or_none() -> None:
    assert client_address(_request("203.0.113.7")) == "203.0.113.7"
    assert client_address(_request(None)) is None


def test_a_forwarded_header_cannot_change_the_key() -> None:
    forged = _request("203.0.113.7", x_forwarded_for="198.51.100.9", forwarded="for=198.51.100.9")

    assert caller_of(forged) == "203.0.113.7"


def test_the_limiters_default_key_is_the_caller_of() -> None:
    assert limiter._key_func is caller_of  # noqa: SLF001 - the one way to see which key it uses


def test_unknown_links_count_an_ipv6_caller_by_its_64() -> None:
    configure_rate_limits(RateLimits(unknown_links="2/hour"), enabled=True)

    first = [count_unknown_link(_request(f"2001:db8:1:2::{n:x}")) for n in (1, 2, 3)]
    other_64 = count_unknown_link(_request("2001:db8:1:3::1"))
    v4 = [count_unknown_link(_request("203.0.113.7")) for _ in range(3)]
    mapped = count_unknown_link(_request("::ffff:203.0.113.7"))

    assert first == [False, False, True]
    assert other_64 is False
    assert v4 == [False, False, True]
    assert mapped is True  # the IPv4 caller's bucket, already over


def test_webhook_buckets_are_counted_under_the_callers_key() -> None:
    configure_rate_limits(RateLimits(webhooks="2/minute"), enabled=True)

    verdicts = [
        within_webhook_limit("wxcc", "acme", caller_of(_request(f"2001:db8:1:2::{n:x}")))
        for n in (1, 2, 3)
    ]
    other = within_webhook_limit("wxcc", "acme", caller_of(_request("2001:db8:1:3::1")))

    assert verdicts == [True, True, False]
    assert other is True
