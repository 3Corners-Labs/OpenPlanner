"""The hop from a reverse proxy to web (ADR 0091 point 3)."""

import pytest

from app.domain.web_transport import BUNDLED_PROXY_ADDRESS, proxy_hop_unencrypted


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
