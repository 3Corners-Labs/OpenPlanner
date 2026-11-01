"""The certificate source block on Advanced settings (ADR 0070)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
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
def volume(tmp_path: Path) -> Path:
    return tmp_path / "portal-certs"


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
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{HOST}") as c,
    ):
        yield Site(c, idp, clock)


def files(chain: bytes, key: bytes | None = None) -> dict[str, tuple[str, bytes, str]]:
    sent = {"chain": ("fullchain.pem", chain, "application/x-pem-file")}
    if key is not None:
        sent["key"] = ("privkey.pem", key, "application/x-pem-file")
    return sent


async def upload(site: Site, chain: bytes, key: bytes | None = None) -> httpx.Response:
    return await site.client.post(
        f"{URL}/tls/certificate", files=files(chain, key), headers=SAME_ORIGIN
    )


async def choose(site: Site, source: str) -> httpx.Response:
    return await site.client.post(
        f"{URL}/tls_certificate_source", data={"value": source}, headers=SAME_ORIGIN
    )


async def audit_details(site: Site) -> list[str]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        rows = await session.scalars(select(AuditEventRow).order_by(AuditEventRow.id))
    return [row.detail for row in rows if row.detail]


def issue(csr_pem: str, *, days: int = 90) -> bytes:
    """Act as the CA: sign the request's key for its name with a throw-away issuer."""
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    issuer = make_pair("ca.example.nl")
    issuer_key = serialization.load_pem_private_key(issuer.key, password=None)
    issuer_name = x509.load_pem_x509_certificate(issuer.chain).subject
    certificate = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(issuer_name)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(issuer_key, hashes.SHA256())  # type: ignore[arg-type]
    )
    return certificate.public_bytes(serialization.Encoding.PEM) + issuer.chain


async def test_the_block_offers_the_source_the_request_and_the_upload_to_the_superuser(
    site: Site,
) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get(URL)).text

    assert "Bron van het certificaat" in text
    assert "Let&#39;s Encrypt (automatisch)" in text
    assert 'action="/admin/settings/tls_certificate_source"' in text
    assert 'action="/admin/settings/tls/csr"' in text
    assert 'action="/admin/settings/tls/certificate"' in text
    assert 'action="/admin/settings/tls/check"' in text
    assert "Er is geen eigen certificaat geüpload." in text
    assert "openplanner.csr" not in text  # no request yet
    assert text.count("tls_certificate_source") < 6  # not also a row in the generic table


async def test_the_auditor_sees_the_block_without_forms(site: Site) -> None:
    await site.login_as(AUDITOR)

    text = (await site.client.get(URL)).text

    assert "Bron van het certificaat" in text
    assert 'action="/admin/settings/tls/csr"' not in text
    assert 'action="/admin/settings/tls/certificate"' not in text
    assert 'action="/admin/settings/tls/check"' not in text
    assert (await site.client.get(f"{URL}/tls/csr.pem")).status_code == 403


async def test_the_default_path_request_issue_place_switch_and_back(
    site: Site, volume: Path
) -> None:
    await site.login_as(SUPER)

    made = await site.client.post(f"{URL}/tls/csr", headers=SAME_ORIGIN)

    assert made.status_code == 303
    page = (await site.client.get(made.headers["location"])).text
    assert "De sleutel en de aanvraag zijn gemaakt." in page
    assert "-----BEGIN CERTIFICATE REQUEST-----" in page
    download = await site.client.get(f"{URL}/tls/csr.pem")
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="openplanner.csr"'
    assert download.text.startswith("-----BEGIN CERTIFICATE REQUEST-----")
    assert (volume / "pending.key").exists()
    assert not (volume / "privkey.pem").exists()
    assert any(d == "tls.key.generate" for d in await audit_details(site))

    placed = await upload(site, issue(download.text, days=45))

    assert placed.status_code == 303
    page = (await site.client.get(placed.headers["location"])).text
    assert "Het certificaat is opgeslagen." in page
    assert "nog 45 dagen" in page
    assert "Aanwezig" in page  # the key on this host
    assert (volume / "privkey.pem").exists()
    assert not (volume / "pending.key").exists()
    assert not (volume / "serial").exists()  # not served: the source is still acme
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        row = (await session.scalars(select(OwnCertificateRow))).one()
    assert (volume / "privkey.pem").read_text() not in page
    assert any(
        d.startswith(f"tls.certificate.upload:{row.fingerprint}:")
        for d in await audit_details(site)
    )

    chosen = await choose(site, "own")

    assert chosen.status_code == 303
    assert (volume / "serial").read_text().strip() == row.fingerprint
    assert (volume / "fullchain.pem").read_text() == row.chain_pem
    text = (await site.client.get(URL)).text
    assert "Eigen certificaat (geüpload)" in text
    assert "Dit certificaat is in gebruik" in text

    back = await choose(site, "acme")

    assert back.status_code == 303
    assert not (volume / "serial").exists()
    assert (volume / "privkey.pem").exists()  # the key stays for the next chain
    assert "Geüpload certificaat verwijderen" in (await site.client.get(URL)).text


async def test_migrating_an_existing_pair_puts_the_key_on_the_volume_only(
    site: Site, volume: Path
) -> None:
    await site.login_as(SUPER)
    pair = make_pair(HOST)

    response = await upload(site, pair.chain, pair.key)

    assert response.status_code == 303
    assert (volume / "privkey.pem").read_bytes() == pair.key
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        row = (await session.scalars(select(OwnCertificateRow))).one()
    assert pair.key.decode() not in row.chain_pem
    assert pair.key.decode() not in (await site.client.get(URL)).text
    details = await audit_details(site)
    assert any(d.startswith("tls.key.import:") for d in details)
    assert all(pair.key.decode() not in d for d in details)


async def test_a_restored_database_shows_the_key_missing_and_refuses_own(
    site: Site, volume: Path
) -> None:
    await site.login_as(SUPER)
    pair = make_pair(HOST)
    await upload(site, pair.chain, pair.key)
    (volume / "privkey.pem").unlink()  # the database came along, the volume did not

    text = (await site.client.get(URL)).text
    chosen = await choose(site, "own")

    assert "Ontbreekt op deze server" in text
    assert chosen.status_code == 422
    assert "staat niet op deze server" in chosen.text


async def test_own_without_a_chain_is_refused_at_the_form(site: Site) -> None:
    await site.login_as(SUPER)

    response = await choose(site, "own")

    assert response.status_code == 422
    assert "Upload eerst een eigen certificaat" in response.text
    assert 'id="error-tls_certificate_source"' in response.text


async def test_a_chain_for_no_key_on_this_host_is_refused_at_the_chain_field(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.post(f"{URL}/tls/csr", headers=SAME_ORIGIN)

    response = await upload(site, make_pair(HOST).chain)

    assert response.status_code == 422
    assert "hoort niet bij een sleutel op deze server" in response.text
    assert 'id="error-tls-chain"' in response.text


async def test_a_chain_for_another_host_is_refused(site: Site) -> None:
    await site.login_as(SUPER)
    pair = make_pair("other.example.nl")

    response = await upload(site, pair.chain, pair.key)

    assert response.status_code == 422
    assert "niet geldig voor het publieke adres" in response.text


async def test_an_imported_key_of_another_certificate_is_refused_at_the_key_field(
    site: Site, volume: Path
) -> None:
    await site.login_as(SUPER)
    pair, other = make_pair(HOST), make_pair(HOST)

    response = await upload(site, pair.chain, other.key)

    assert response.status_code == 422
    assert 'id="error-tls-key"' in response.text
    assert not (volume / "privkey.pem").exists()


async def test_garbage_and_oversized_uploads_are_refused_without_echo(site: Site) -> None:
    await site.login_as(SUPER)

    response = await upload(site, b"SECRET-LOOKING-GARBAGE")
    assert response.status_code == 422
    assert "geen leesbaar PEM-bestand" in response.text
    assert "SECRET-LOOKING-GARBAGE" not in response.text

    pair = make_pair(HOST)
    too_big = await upload(site, pair.chain + b"\n" * 70_000)
    assert too_big.status_code in (413, 422)


async def test_removing_is_refused_while_in_use_and_works_after_switching_back(
    site: Site, volume: Path
) -> None:
    await site.login_as(SUPER)
    pair = make_pair(HOST)
    await upload(site, pair.chain, pair.key)
    await choose(site, "own")

    refused = await site.client.post(f"{URL}/tls/certificate/remove", headers=SAME_ORIGIN)

    assert refused.status_code == 409
    assert "kies eerst Let&#39;s Encrypt als bron" in refused.text

    await choose(site, "acme")
    removed = await site.client.post(f"{URL}/tls/certificate/remove", headers=SAME_ORIGIN)

    assert removed.status_code == 303
    assert "done=certificate_removed" in removed.headers["location"]
    assert "Er is geen eigen certificaat geüpload." in (await site.client.get(URL)).text
    assert (volume / "privkey.pem").exists()
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


@pytest.fixture
def lock_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TLS_CERTIFICATE_SOURCE", "acme")


@pytest.mark.usefixtures("lock_source")
async def test_an_environment_lock_hides_the_forms_and_refuses_a_change(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get(URL)).text
    chosen = await choose(site, "acme")

    assert "<code>TLS_CERTIFICATE_SOURCE</code>" in text
    assert "beheert het certificaat op de server" in text
    assert 'action="/admin/settings/tls/certificate"' not in text
    assert 'action="/admin/settings/tls/csr"' not in text
    assert chosen.status_code == 422


async def test_an_org_admin_gets_nothing(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    assert (await upload(site, make_pair(HOST).chain)).status_code == 403
    assert (await site.client.post(f"{URL}/tls/csr", headers=SAME_ORIGIN)).status_code == 403
    assert (await site.client.get(f"{URL}/tls/csr.pem")).status_code == 403
    assert (await site.client.post(f"{URL}/tls/check", headers=SAME_ORIGIN)).status_code == 403


async def test_cross_site_posts_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(f"{URL}/tls/csr", headers={"sec-fetch-site": "cross-site"})

    assert response.status_code == 403
