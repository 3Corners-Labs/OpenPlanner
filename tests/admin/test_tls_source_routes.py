"""The certificate source block on Advanced settings (ADR 0066)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import AuditEventRow, OwnCertificateRow
from app.adapters.db.seed import seed_demo
from app.config import Settings
from app.domain.tls import Certificate, ProbeResult, Trust
from app.main import create_app
from tests.admin.conftest import AUDITOR, ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.fakes.clock import FixedClock
from tests.fakes.identity import FakeIdentityProvider
from tests.fakes.tls import FakeCertificateProbe
from tests.support.certificates import make_pair
from tests.support.domain import NOW

HOST = "afspraak.example.nl"
URL = "/admin/settings"


@pytest.fixture
def settings(settings: Settings, tmp_path: Path) -> Settings:
    """A small installation: the bundled proxy runs on a public https address."""
    return settings.model_copy(
        update={
            "public_base_url": f"https://{HOST}",
            "tls_proxy_bundled": True,
            "tls_portal_certificate_dir": tmp_path / "portal-certs",
        }
    )


@pytest.fixture
def probe() -> FakeCertificateProbe:
    return FakeCertificateProbe()


@pytest.fixture
async def site(
    settings: Settings, admin_db: str, probe: FakeCertificateProbe
) -> AsyncIterator[Site]:
    engine = create_engine(admin_db)
    await seed_demo(engine)
    await engine.dispose()
    idp, clock = FakeIdentityProvider(), FixedClock(NOW)
    app = create_app(
        settings.model_copy(update={"database_url": admin_db}), clock, idp, certificate_probe=probe
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as c,
    ):
        yield Site(c, idp, clock)


def files(chain: bytes, key: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {
        "chain": ("fullchain.pem", chain, "application/x-pem-file"),
        "key": ("privkey.pem", key, "application/x-pem-file"),
    }


async def upload(site: Site, chain: bytes, key: bytes) -> httpx.Response:
    return await site.client.post(
        f"{URL}/tls/certificate", files=files(chain, key), headers=SAME_ORIGIN
    )


async def audit_details(site: Site) -> list[str]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        rows = await session.scalars(select(AuditEventRow).order_by(AuditEventRow.id))
    return [row.detail for row in rows if row.detail]


async def test_the_block_offers_the_source_and_the_upload_to_the_superuser(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get(URL)).text

    assert "Bron van het certificaat" in text
    assert "Let&#39;s Encrypt (automatisch)" in text
    assert 'action="/admin/settings/tls_certificate_source"' in text
    assert 'action="/admin/settings/tls/certificate"' in text
    assert 'action="/admin/settings/tls/check"' in text
    assert "Er is geen eigen certificaat geüpload." in text
    assert 'id="setting-tls_certificate_source"' in text
    assert text.count("tls_certificate_source") < 6  # not also a row in the generic table


async def test_the_auditor_sees_the_block_without_forms(site: Site) -> None:
    await site.login_as(AUDITOR)

    text = (await site.client.get(URL)).text

    assert "Bron van het certificaat" in text
    assert 'action="/admin/settings/tls/certificate"' not in text
    assert 'action="/admin/settings/tls/check"' not in text


async def test_an_upload_is_stored_shown_delivered_when_chosen_and_audited(
    site: Site, tmp_path: Path
) -> None:
    await site.login_as(SUPER)
    pair = make_pair(HOST, not_after=NOW + timedelta(days=45))
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await upload(site, pair.chain, pair.key)

    assert response.status_code == 303
    assert "done=certificate_uploaded" in response.headers["location"]
    text = (await site.client.get(response.headers["location"])).text
    assert "Het certificaat is opgeslagen." in text
    assert f"Gemeente Demo ({HOST})" in text
    assert "nog 45 dagen" in text
    assert "Geüpload certificaat verwijderen" in text  # removable while ACME is the source
    assert pair.key.decode() not in text
    async with AsyncSession(app.state.engine) as session:
        row = (await session.scalars(select(OwnCertificateRow))).one()
    assert pair.key not in row.key_ciphertext
    assert not (tmp_path / "portal-certs" / "serial").exists()  # not delivered: still acme
    details = await audit_details(site)
    assert any(d.startswith(f"tls.certificate.upload:{row.fingerprint}:") for d in details)
    assert all(pair.key.decode() not in d for d in details)

    # Choosing the own certificate stores the setting and hands the pair to the proxy.
    chosen = await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": "own"}, headers=SAME_ORIGIN
    )

    assert chosen.status_code == 303
    directory = tmp_path / "portal-certs"
    assert (directory / "serial").read_text().strip() == row.fingerprint
    assert (directory / "privkey.pem").read_bytes() == pair.key
    assert (directory / "fullchain.pem").read_bytes() == pair.chain
    text = (await site.client.get(URL)).text
    assert "Eigen certificaat (geüpload)" in text
    assert "Dit certificaat is in gebruik" in text
    assert "set:tls_certificate_source:acme-&gt;own" in text or any(
        "set:tls_certificate_source:acme->own" in d for d in await audit_details(site)
    )

    # Back to ACME withdraws the pair but keeps it stored.
    back = await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": "acme"}, headers=SAME_ORIGIN
    )

    assert back.status_code == 303
    assert not (directory / "serial").exists()
    assert not (directory / "privkey.pem").exists()
    assert f"Gemeente Demo ({HOST})" in (await site.client.get(URL)).text


async def test_own_without_an_upload_is_refused_at_the_form(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": "own"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert "Upload eerst een eigen certificaat" in response.text
    assert 'id="error-tls_certificate_source"' in response.text


async def test_a_pair_for_another_host_is_refused_at_the_chain_field(site: Site) -> None:
    await site.login_as(SUPER)
    pair = make_pair("other.example.nl")

    response = await upload(site, pair.chain, pair.key)

    assert response.status_code == 422
    assert "niet geldig voor het publieke adres" in response.text
    assert 'id="error-tls-chain"' in response.text


async def test_a_key_of_another_certificate_is_refused_at_the_key_field(site: Site) -> None:
    await site.login_as(SUPER)
    pair, other = make_pair(HOST), make_pair(HOST)

    response = await upload(site, pair.chain, other.key)

    assert response.status_code == 422
    assert "hoort niet bij het servercertificaat" in response.text
    assert 'id="error-tls-key"' in response.text


async def test_garbage_and_oversized_uploads_are_refused_without_echo(site: Site) -> None:
    await site.login_as(SUPER)

    response = await upload(site, b"SECRET-LOOKING-GARBAGE", b"nor a key")
    assert response.status_code == 422
    assert "geen leesbaar PEM-bestand" in response.text
    assert "SECRET-LOOKING-GARBAGE" not in response.text

    pair = make_pair(HOST)
    too_big = await upload(site, pair.chain + b"\n" * 70_000, pair.key)
    assert too_big.status_code in (413, 422)


async def test_removing_is_refused_while_in_use_and_works_after_switching_back(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    pair = make_pair(HOST)
    await upload(site, pair.chain, pair.key)
    await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": "own"}, headers=SAME_ORIGIN
    )

    refused = await site.client.post(f"{URL}/tls/certificate/remove", headers=SAME_ORIGIN)

    assert refused.status_code == 409
    assert "kies eerst Let&#39;s Encrypt als bron" in refused.text

    await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": "acme"}, headers=SAME_ORIGIN
    )
    removed = await site.client.post(f"{URL}/tls/certificate/remove", headers=SAME_ORIGIN)

    assert removed.status_code == 303
    assert "done=certificate_removed" in removed.headers["location"]
    assert "Er is geen eigen certificaat geüpload." in (await site.client.get(URL)).text
    assert any(d.startswith("tls.certificate.remove:") for d in await audit_details(site))


async def test_check_now_probes_and_shows_the_served_certificate(
    site: Site, probe: FakeCertificateProbe
) -> None:
    await site.login_as(SUPER)
    probe.result = ProbeResult(
        Certificate(
            HOST,
            "Staat der Nederlanden",
            NOW - timedelta(days=1),
            NOW + timedelta(days=300),
            (HOST,),
        ),
        Trust.TRUSTED,
    )

    response = await site.client.post(f"{URL}/tls/check", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert probe.asked == [(HOST, 443)]
    text = (await site.client.get(response.headers["location"])).text
    assert "Het certificaat is gecontroleerd." in text
    assert "Staat der Nederlanden" in text
    assert "In orde" in text


async def test_an_org_admin_gets_nothing(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    pair = make_pair(HOST)

    assert (await upload(site, pair.chain, pair.key)).status_code == 403
    assert (await site.client.post(f"{URL}/tls/check", headers=SAME_ORIGIN)).status_code == 403


async def test_cross_site_posts_are_refused(site: Site) -> None:
    await site.login_as(SUPER)
    pair = make_pair(HOST)

    response = await site.client.post(
        f"{URL}/tls/certificate",
        files=files(pair.chain, pair.key),
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403


@pytest.fixture
def lock_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TLS_CERTIFICATE_SOURCE", "acme")


@pytest.mark.usefixtures("lock_source")
async def test_an_environment_lock_hides_the_forms_and_refuses_a_change(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get(URL)).text
    chosen = await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": "acme"}, headers=SAME_ORIGIN
    )

    assert "<code>TLS_CERTIFICATE_SOURCE</code>" in text
    assert "beheert het certificaat op de server" in text
    assert 'action="/admin/settings/tls/certificate"' not in text
    assert chosen.status_code == 422
