"""The one reader of who is calling: the key every limiter counts, and the forwarded headers.

An IPv6 caller is counted by its /64 (ADR 0124 step 2a); what the server did with a request's
forwarded headers is read in one place (ADR 0086, ADR 0124 point 4).
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.domain.public_address import Forwarded
from app.web.caller import caller_of, client_address, forwarded_headers_ignored, forwarded_of
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


def _request(
    address: str | None,
    *pairs: tuple[str, str],
    scheme: str = "http",
    **headers: str,
) -> Request:
    """A request from ``address``.

    ``pairs`` are header lines (a name may repeat), ``headers`` one each.
    """
    lines = [*pairs, *((k.replace("_", "-"), v) for k, v in headers.items())]
    return Request(
        {
            "type": "http",
            "app": FastAPI(),
            "scheme": scheme,
            "server": ("planner.voorbeeld.nl", 80),
            "path": "/x",
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in lines],
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


@pytest.mark.parametrize(
    ("client", "headers", "scheme", "expected"),
    [
        ("8.8.8.8", (), "http", Forwarded.ABSENT),
        # the server used the header: the client is a hop of it
        ("8.8.8.8", (("x-forwarded-for", "8.8.8.8, 172.31.240.2"),), "http", Forwarded.HONOURED),
        # the header is there and the client is still the peer: its peer was not trusted
        ("8.8.4.4", (("x-forwarded-for", "8.8.8.8"),), "http", Forwarded.IGNORED),
        ("8.8.4.4", (("x-forwarded-proto", "https"),), "http", Forwarded.IGNORED),
        ("8.8.4.4", (("x-forwarded-proto", "https"),), "https", Forwarded.HONOURED),
        # ``Forwarded`` is never used by the server, so it is not judged
        ("8.8.4.4", (("forwarded", "for=8.8.8.8"),), "http", Forwarded.ABSENT),
        (None, (("x-forwarded-for", "8.8.8.8"),), "http", Forwarded.IGNORED),
    ],
)
def test_what_the_server_did_with_this_requests_forwarded_headers(
    client: str | None,
    headers: tuple[tuple[str, str], ...],
    scheme: str,
    expected: Forwarded,
) -> None:
    asked = _request(client, *headers, scheme=scheme)

    assert forwarded_of(asked) is expected
    assert forwarded_headers_ignored(asked) is (expected is Forwarded.IGNORED)


HONOURED_LINE = (
    "x-forwarded-for",
    "8.8.8.8, 172.31.240.2",
)  # the client 8.8.8.8 is one of its hops
IGNORED_LINE = ("x-forwarded-for", "8.8.4.4")  # the client is not
PROTO_HONOURED = ("x-forwarded-proto", "https")
PROTO_IGNORED = ("x-forwarded-proto", "http")


@pytest.mark.parametrize(
    "headers",
    [
        (HONOURED_LINE, IGNORED_LINE),
        (IGNORED_LINE, HONOURED_LINE),
        (HONOURED_LINE, HONOURED_LINE, IGNORED_LINE),
        (PROTO_HONOURED, PROTO_IGNORED),
        (PROTO_IGNORED, PROTO_HONOURED),
        (HONOURED_LINE, PROTO_HONOURED, IGNORED_LINE),
    ],
)
def test_when_forwarded_headers_disagree_the_least_safe_answer_wins_whichever_comes_first(
    headers: tuple[tuple[str, str], ...],
) -> None:
    """Several lines of a kind: judged one by one, *ignored* beats *honoured*, in either order."""
    asked = _request("8.8.8.8", *headers, scheme="https")

    assert forwarded_of(asked) is Forwarded.IGNORED
    assert forwarded_headers_ignored(asked)


def test_several_lines_that_all_agree_are_judged_as_one() -> None:
    honoured = _request("8.8.8.8", HONOURED_LINE, HONOURED_LINE, PROTO_HONOURED, scheme="https")

    assert forwarded_of(honoured) is Forwarded.HONOURED
