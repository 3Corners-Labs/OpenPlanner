"""The host's trust in the proxy's internal certificate authority (ADR 0112)."""

from pathlib import Path

import httpx

from app.adapters import internal_ca
from app.config import Settings


def configured(settings: Settings, root: Path) -> Settings:
    return settings.model_copy(update={"tls_internal_ca_file": root})


def test_without_the_file_nothing_changes(settings: Settings, tmp_path: Path) -> None:
    absent = configured(settings, tmp_path / "root.crt")

    assert internal_ca.root_file(absent) is None
    assert internal_ca.certificate_probe(absent)._extra_cafile() is None  # type: ignore[operator]  # noqa: SLF001


def test_the_published_root_is_what_the_probe_trusts(settings: Settings, tmp_path: Path) -> None:
    root = tmp_path / "root.crt"
    root.write_text("-----BEGIN CERTIFICATE-----\n")
    present = configured(settings, root)

    assert internal_ca.root_file(present) == root
    assert internal_ca.certificate_probe(present)._extra_cafile() == str(root)  # type: ignore[operator]  # noqa: SLF001


def test_a_given_transport_wins_over_the_root(settings: Settings, tmp_path: Path) -> None:
    """Tests (and the CLI's --ca-file) hand in a transport; the root must not replace it."""
    root = tmp_path / "root.crt"
    root.write_text("not a certificate")
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    probe = internal_ca.instance_probe(configured(settings, root), transport)

    assert probe._transport is transport  # noqa: SLF001


def test_a_root_published_after_the_probe_was_built_is_trusted(
    settings: Settings, tmp_path: Path
) -> None:
    # Release audit v0.4.0 P2 (#515): the proxy publishes the root after web has started.
    root = tmp_path / "root.crt"
    configured_settings = configured(settings, root)
    certificates = internal_ca.certificate_probe(configured_settings)
    addresses = internal_ca.instance_probe(configured_settings)
    assert certificates._extra_cafile() is None  # type: ignore[operator]  # noqa: SLF001
    assert addresses._transport_factory is not None  # noqa: SLF001
    assert addresses._transport_factory() is None  # noqa: SLF001

    root.write_text("-----BEGIN CERTIFICATE-----\n")  # published later

    assert certificates._extra_cafile() == str(root)  # type: ignore[operator]  # noqa: SLF001


async def test_an_unreadable_root_is_a_tls_answer_not_a_crash(
    settings: Settings, tmp_path: Path
) -> None:
    root = tmp_path / "root.crt"
    root.write_text("not a certificate")

    reply = await internal_ca.instance_probe(configured(settings, root)).probe(
        "https://planner.example.test", "n"
    )

    assert reply.error == "tls"
