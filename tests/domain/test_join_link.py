"""The link a citizen is sent into the Webex meeting, per group (ADR 0075)."""

from urllib.parse import parse_qs, urlsplit

import pytest

from app.domain.calls import BROWSER_LINK_PARAMS, join_link
from app.domain.scheduling import JoinClient
from tests.support.domain import make_group

SHORT = (
    "https://rijksvideotest.webex.com/rijksvideotest/j.php?MTID=m142bcf67f73207da1acdf0c757ae77ca"
)


def test_a_group_opens_the_browser_unless_it_says_otherwise() -> None:
    assert make_group().join_client is JoinClient.BROWSER
    assert make_group(join_client=JoinClient.CHOICE).join_client is JoinClient.CHOICE


def test_the_choice_link_is_the_stored_link_unchanged() -> None:
    assert join_link(SHORT, JoinClient.CHOICE) == SHORT


def test_the_browser_link_adds_the_parameter_and_keeps_the_rest() -> None:
    link = join_link(SHORT, JoinClient.BROWSER)

    parts = urlsplit(link)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https",
        "rijksvideotest.webex.com",
        "/rijksvideotest/j.php",
    )
    assert parse_qs(parts.query) == {
        "MTID": ["m142bcf67f73207da1acdf0c757ae77ca"],
        **{name: [value] for name, value in BROWSER_LINK_PARAMS},
    }


def test_the_browser_link_is_the_same_when_applied_twice() -> None:
    once = join_link(SHORT, JoinClient.BROWSER)

    assert join_link(once, JoinClient.BROWSER) == once


def test_the_browser_link_replaces_a_parameter_that_says_the_opposite() -> None:
    link = join_link(SHORT + "&launchApp=false", JoinClient.BROWSER)

    assert parse_qs(urlsplit(link).query)["launchApp"] == ["true"]


def test_the_browser_link_works_on_a_link_without_a_query() -> None:
    link = join_link("https://example.webex.com/meet/room", JoinClient.BROWSER)

    assert link == "https://example.webex.com/meet/room?launchApp=true"


@pytest.mark.parametrize("client", list(JoinClient))
def test_a_link_that_is_not_https_is_never_handed_out(client: JoinClient) -> None:
    assert join_link("http://evil.example/j.php?MTID=x", client) is None
    assert join_link("javascript:alert(1)", client) is None
    assert join_link(None, client) is None
    assert join_link("", client) is None
