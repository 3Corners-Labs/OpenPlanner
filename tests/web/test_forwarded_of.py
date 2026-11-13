"""The one reader of the forwarded headers (ADR 0086, ADR 0124 point 4)."""

import pytest
from starlette.requests import Request

from app.domain.public_address import Forwarded
from app.web.caller import forwarded_headers_ignored, forwarded_of


def request(client: str | None, *headers: tuple[str, str], scheme: str = "http") -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": ("planner.voorbeeld.nl", 80),
            "path": "/",
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in headers],
            "client": (client, 4711) if client else None,
        }
    )


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
    asked = request(client, *headers, scheme=scheme)

    assert forwarded_of(asked) is expected
    assert forwarded_headers_ignored(asked) is (expected is Forwarded.IGNORED)
