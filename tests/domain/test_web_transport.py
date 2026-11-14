"""The hops inside one host that stay plain HTTP (ADR 0091 point 3, ADR 0125)."""

import pytest

from app.domain.web_transport import (
    BUNDLED_IDP_DISCOVERY_URL,
    BUNDLED_PROXY_ADDRESS,
    bundled_idp_hop,
    proxy_hop_unencrypted,
)


@pytest.mark.parametrize(
    "trusted", [None, "", "  ", BUNDLED_PROXY_ADDRESS, f" {BUNDLED_PROXY_ADDRESS} "]
)
def test_the_bundled_proxy_or_none_is_the_recorded_exception(trusted: str | None) -> None:
    assert proxy_hop_unencrypted(trusted, web_tls=False) is False


@pytest.mark.parametrize(
    "trusted", ["10.0.0.5", "10.0.0.5,10.0.0.6", f"{BUNDLED_PROXY_ADDRESS},10.0.0.5", "*"]
)
def test_another_proxy_without_tls_is_flagged(trusted: str) -> None:
    assert proxy_hop_unencrypted(trusted, web_tls=False) is True


@pytest.mark.parametrize("trusted", ["10.0.0.5", "*", None])
def test_tls_to_web_clears_the_flag(trusted: str | None) -> None:
    assert proxy_hop_unencrypted(trusted, web_tls=True) is False


ISSUER = "https://afspraak.example.nl/idp/realms/openplanner"


def test_the_bundled_provider_behind_the_proxy_is_the_recorded_exception_adr_0125() -> None:
    assert bundled_idp_hop(BUNDLED_IDP_DISCOVERY_URL, ISSUER) is True


@pytest.mark.parametrize("issuer", [None, "", "http://afspraak.example.nl/idp/realms/openplanner"])
def test_without_an_https_issuer_the_bundled_address_is_no_exception(issuer: str | None) -> None:
    """The issuer is where the browser goes: an http one would carry the password in clear."""
    assert bundled_idp_hop(BUNDLED_IDP_DISCOVERY_URL, issuer) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://idp.example.nl/idp/realms/openplanner/.well-known/openid-configuration",
        "http://idp:8081/idp/realms/openplanner/.well-known/openid-configuration",
        "http://idp:8080/idp/realms/other/.well-known/openid-configuration",
        "http://idp:8080/realms/openplanner/.well-known/openid-configuration",
        "http://idp:8080@idp.example.nl/idp/realms/openplanner/.well-known/openid-configuration",
        f"{BUNDLED_IDP_DISCOVERY_URL}?next=http://idp.example.nl",
        BUNDLED_IDP_DISCOVERY_URL.upper(),
    ],
)
def test_any_other_http_discovery_url_is_no_exception(url: str) -> None:
    """Keyed on the address the render script writes, never on plain http as such."""
    assert bundled_idp_hop(url, ISSUER) is False
