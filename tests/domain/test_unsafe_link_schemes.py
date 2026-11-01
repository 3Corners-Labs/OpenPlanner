"""Links an administrator sets refuse script and data schemes (ASVS V1.2.2, V5.4.2).

A link entered in the portal ends up in an ``href``, a redirect or a request. Whatever way it is
spelled (case, white space, control characters), ``javascript:``, ``data:``, ``vbscript:`` and
``file:`` never pass. The ``javascript:`` case is also covered per feature elsewhere; this
sweeps every setter of such a link with the same list.
"""

from collections.abc import Callable

import pytest

from app.domain.calls import JoinClient, join_link
from app.domain.contacts import ContactKind, ContactOption
from app.domain.embedding import normalise_origin
from app.domain.errors import DomainError
from app.domain.identity_providers import is_discovery_url
from app.domain.updates import Release, Version

DISCOVERY = "/.well-known/openid-configuration"
UNSAFE = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    " javascript:alert(1)",
    "\tjava\nscript:alert(1)",
    "javascript://www.gemeente.nl/%0aalert(1)",
    "data:text/html,<script>alert(1)</script>",
    "DATA:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "data://www.gemeente.nl/x",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "//evil.example/x",
]


def _contact(kind: ContactKind) -> Callable[[str], object]:
    return lambda value: ContactOption.create(kind, value)


def _is_refused(build: Callable[[str], object], value: str) -> bool:
    try:
        build(value)
    except DomainError:
        return True
    return False


@pytest.mark.parametrize("value", UNSAFE)
@pytest.mark.parametrize("kind", [ContactKind.WEBSITE, ContactKind.CHAT], ids=lambda k: k.value)
def test_a_contact_link_with_a_script_or_data_scheme_is_refused(
    kind: ContactKind, value: str
) -> None:
    assert _is_refused(_contact(kind), value)


@pytest.mark.parametrize("value", UNSAFE)
def test_a_release_page_with_a_script_or_data_scheme_is_refused(value: str) -> None:
    assert _is_refused(lambda url: Release(Version(2, 0, 0), url), value)


@pytest.mark.parametrize("value", UNSAFE)
def test_an_allowed_origin_with_a_script_or_data_scheme_is_refused(value: str) -> None:
    assert normalise_origin(value, allow_local_http=False) is None
    assert normalise_origin(value, allow_local_http=True) is None


@pytest.mark.parametrize("value", UNSAFE)
def test_an_identity_provider_address_with_a_script_or_data_scheme_is_refused(value: str) -> None:
    assert not is_discovery_url(value)
    assert not is_discovery_url(value + DISCOVERY)


@pytest.mark.parametrize("client", list(JoinClient))
@pytest.mark.parametrize("value", UNSAFE)
def test_a_join_link_with_a_script_or_data_scheme_is_never_handed_out(
    client: JoinClient, value: str
) -> None:
    assert join_link(value, client) is None
