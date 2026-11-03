"""The host's trust in the proxy's internal certificate authority (ADR 0112)."""

from pathlib import Path

import httpx
import pytest

from app.adapters import internal_ca
from app.config import Settings


def configured(settings: Settings, root: Path) -> Settings:
    return settings.model_copy(update={"tls_internal_ca_file": root})


def test_without_the_file_nothing_changes(settings: Settings, tmp_path: Path) -> None:
    absent = configured(settings, tmp_path / "root.crt")

    assert internal_ca.root_file(absent) is None
    assert internal_ca.certificate_probe(absent)._extra_cafile is None  # noqa: SLF001


def test_the_published_root_is_what_the_probe_trusts(settings: Settings, tmp_path: Path) -> None:
    root = tmp_path / "root.crt"
    root.write_text("-----BEGIN CERTIFICATE-----\n")
    present = configured(settings, root)

    assert internal_ca.root_file(present) == root
    assert internal_ca.certificate_probe(present)._extra_cafile == str(root)  # noqa: SLF001


def test_a_given_transport_wins_over_the_root(settings: Settings, tmp_path: Path) -> None:
    """Tests (and the CLI's --ca-file) hand in a transport; the root must not replace it."""
    root = tmp_path / "root.crt"
    root.write_text("not a certificate")
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    probe = internal_ca.instance_probe(configured(settings, root), transport)

    assert probe._transport is transport  # noqa: SLF001


def test_an_unreadable_root_is_an_error_not_silence(settings: Settings, tmp_path: Path) -> None:
    root = tmp_path / "root.crt"
    root.write_text("not a certificate")

    with pytest.raises(Exception, match=r"."):
        internal_ca.instance_probe(configured(settings, root))
