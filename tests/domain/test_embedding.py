"""Allowed host origins (ADR 0035 §3): exact scheme, host and port, no wildcards."""

import pytest

from app.domain.embedding import AllowedOrigins, normalise_origin, parse_origins
from app.domain.errors import DomainError, ErrorCode


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.gemeente.nl", "https://www.gemeente.nl"),
        ("  https://www.gemeente.nl/  ", "https://www.gemeente.nl"),
        ("HTTPS://WWW.Gemeente.NL", "https://www.gemeente.nl"),
        ("https://www.gemeente.nl:443", "https://www.gemeente.nl"),
        ("https://www.gemeente.nl:8443", "https://www.gemeente.nl:8443"),
        ("https://xn--gemeente-9ya.nl", "https://xn--gemeente-9ya.nl"),
        ("https://10.0.0.5", "https://10.0.0.5"),
    ],
)
def test_https_origins_are_normalised_to_what_a_browser_sends(text: str, expected: str) -> None:
    assert normalise_origin(text, allow_local_http=False) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "www.gemeente.nl",
        "http://www.gemeente.nl",
        "https://*.gemeente.nl",
        "*",
        "https://www.gemeente.nl/afspraak",
        "https://www.gemeente.nl/?a=1",
        "https://www.gemeente.nl/#top",
        "https://user@www.gemeente.nl",
        "https://www.gemeente.nl:",
        "https://www.gemeente.nl:0",
        "https://www.gemeente.nl:70000",
        "https://www.gemeente.nl:+1",
        "https://www.gemeente.nl.",
        "https://-gemeente.nl",
        "https://gemeente_x.nl",
        "https://gëmeente.nl",
        "https://www.ge meente.nl",
        "https://[2001:db8::1]",
        "https:\\\\www.gemeente.nl",
        "javascript://www.gemeente.nl",
        "null",
        "https://" + "a" * 200 + ".nl",
        "https://localhost",
        "http://localhost:5173",
    ],
)
def test_anything_else_is_not_an_origin(text: str) -> None:
    assert normalise_origin(text, allow_local_http=False) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("http://localhost:5173", "http://localhost:5173"),
        ("http://127.0.0.1:8001/", "http://127.0.0.1:8001"),
        ("http://[::1]:8001", "http://[::1]:8001"),
        ("http://localhost:80", "http://localhost"),
        ("https://localhost", "https://localhost"),
    ],
)
def test_local_hosts_only_where_allowed(text: str, expected: str) -> None:
    assert normalise_origin(text, allow_local_http=True) == expected
    assert normalise_origin(text, allow_local_http=False) is None


def test_local_http_does_not_open_http_for_other_hosts() -> None:
    assert normalise_origin("http://www.gemeente.nl", allow_local_http=True) is None
    assert normalise_origin("ftp://localhost", allow_local_http=True) is None


def test_a_textarea_is_parsed_line_by_line_skipping_blanks_and_repeats() -> None:
    text = "https://www.gemeente.nl\n\n  https://werk.gemeente.nl/ \r\nhttps://WWW.gemeente.nl\n"

    found = parse_origins(text, allow_local_http=False, limit=10)

    assert found == AllowedOrigins(("https://www.gemeente.nl", "https://werk.gemeente.nl"))


def test_the_first_bad_line_is_named_by_its_line_number() -> None:
    with pytest.raises(DomainError) as refused:
        parse_origins(
            "https://www.gemeente.nl\n\nhttps://*.gemeente.nl\nftp://x",
            allow_local_http=False,
            limit=10,
        )

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "origin_3",
    )


def test_more_origins_than_the_limit_are_refused() -> None:
    text = "\n".join(f"https://site{n}.gemeente.nl" for n in range(4))

    assert len(parse_origins(text, allow_local_http=False, limit=4).origins) == 4
    with pytest.raises(DomainError) as refused:
        parse_origins(text, allow_local_http=False, limit=3)
    assert refused.value.field == "origins_count"


def test_an_empty_textarea_is_an_empty_list() -> None:
    assert parse_origins(" \n\n", allow_local_http=False, limit=1) == AllowedOrigins()


def test_allows_is_an_exact_match() -> None:
    allowed = AllowedOrigins(("https://www.gemeente.nl", "http://localhost:5173"))

    assert allowed.allows("https://www.gemeente.nl")
    assert allowed.allows("http://localhost:5173")
    assert not allowed.allows("https://www.gemeente.nl.evil.example")
    assert not allowed.allows("https://gemeente.nl")
    assert not allowed.allows("http://www.gemeente.nl")
    assert not allowed.allows("null")
    assert not allowed.allows(None)
