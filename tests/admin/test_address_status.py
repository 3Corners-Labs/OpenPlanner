"""The *Address and proxy* part of System status and its row on the overview (ADR 0086)."""

from typing import Any

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.public_address import AddressCheck, AddressFinding, AddressKind, Forwarded
from tests.admin.conftest import AUDITOR, SUPER, Site

PUBLIC = "https://afspraak.example.nl"


def _app(site: Site) -> Any:
    return site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001


async def _save(site: Site, **update: Any) -> None:
    base = AddressCheck(
        AddressKind.PUBLIC, site.clock.now(), PUBLIC, AddressFinding.OK, "https", Forwarded.HONOURED
    )
    check = AddressCheck(**{**base.__dict__, **update})
    async with SqlUnitOfWork(_app(site).state.engine).locked() as tx:
        await tx.save_address_check(check)


def _settings(site: Site, **update: Any) -> None:
    app = _app(site)
    app.state.settings = app.state.settings.model_copy(update={"public_base_url": PUBLIC, **update})


async def _part(site: Site) -> str:
    text = (await site.client.get("/admin/settings")).text
    return text.split('id="address-heading"')[1].split('id="tls-heading"')[0]


async def test_the_part_names_the_shape_the_addresses_and_the_links_to_compare(site: Site) -> None:
    _settings(site, forwarded_allow_ips="10.0.0.5", webhook_base_url="https://hooks.example.nl")
    await site.login_as(AUDITOR)

    part = await _part(site)

    assert "Eigen reverse proxy" in part
    assert f"<code>{PUBLIC}</code>" in part
    assert "<code>https://hooks.example.nl</code>" in part
    assert "<code>10.0.0.5</code>" in part
    assert f"<code>{PUBLIC}/admin/callback</code>" in part
    assert f"<code>{PUBLIC}/widget/v1/gov-booking.js</code>" in part
    assert "Nog niet gecontroleerd" in part


async def test_the_bundled_proxy_shows_its_address_and_no_proxy_says_so(site: Site) -> None:
    _settings(site, tls_proxy_bundled=True)
    await site.login_as(SUPER)
    bundled = await _part(site)
    _settings(site, tls_proxy_bundled=False)
    none = await _part(site)

    assert "Meegeleverde proxy" in bundled
    assert "<code>172.31.240.2</code>" in bundled
    assert "Geen proxy" in none
    assert "geen" in none


async def test_a_webhook_address_equal_to_the_public_one_is_not_shown_twice(site: Site) -> None:
    _settings(site, webhook_base_url=PUBLIC + "/")
    await site.login_as(SUPER)

    assert "Webhook-adres" not in await _part(site)


async def test_a_finding_shows_with_its_time_the_reason_and_what_the_app_saw(site: Site) -> None:
    _settings(site)
    await _save(
        site,
        finding=AddressFinding.PROXY_NOT_TRUSTED,
        scheme="http",
        forwarded=Forwarded.IGNORED,
    )
    await site.login_as(SUPER)

    part = await _part(site)

    assert "chip--warning" in part
    assert "De proxy wordt niet vertrouwd" in part
    assert "De app zag schema http, doorgestuurde headers ignored" in part


async def test_an_unreachable_address_names_the_networks_reason(site: Site) -> None:
    _settings(site)
    await _save(site, finding=AddressFinding.UNREACHABLE, scheme=None, forwarded=None, error="tls")
    await site.login_as(SUPER)

    part = await _part(site)

    assert "chip--danger" in part
    assert "TLS-handshake of certificaat geweigerd" in part


async def test_a_check_of_an_earlier_address_is_not_shown_for_the_new_one(site: Site) -> None:
    _settings(site, public_base_url="https://nieuw.example.nl")
    await _save(site, finding=AddressFinding.OTHER_INSTANCE)
    await site.login_as(SUPER)

    part = await _part(site)

    assert "Nog niet gecontroleerd" in part
    assert "Een andere installatie antwoordt" not in part


async def test_a_switched_off_probe_says_so_and_never_ok(site: Site) -> None:
    _settings(site, public_address_check_enabled=False)
    await _save(site)
    await site.login_as(SUPER)

    part = await _part(site)

    assert "Niet gecontroleerd: uitgezet op de server" in part
    assert "chip--success" not in part


async def test_forwarded_headers_that_were_ignored_are_shown_by_category_and_count(
    site: Site,
) -> None:
    _settings(site)
    watch = _app(site).state.proxy_watch
    for peer in ("172.18.0.1", "172.18.0.1", "8.8.4.4"):
        watch.observe(peer)
    await site.login_as(SUPER)

    part = await _part(site)

    assert "3 verzoek(en)" in part
    assert "van een publiek adres" in part
    assert "8.8.4.4" not in part
    assert 'role="alert"' in part


async def test_a_private_proxy_address_is_shown_because_it_is_the_proxy(site: Site) -> None:
    _settings(site)
    _app(site).state.proxy_watch.observe("172.18.0.1")
    await site.login_as(SUPER)

    assert "van 172.18.0.1" in await _part(site)


async def test_the_unencrypted_hop_warning_is_part_of_the_block_not_a_second_one(
    site: Site,
) -> None:
    _settings(site, forwarded_allow_ips="10.0.0.5")
    await site.login_as(SUPER)

    status = await _part(site)
    overview = (await site.client.get("/admin/overview/w/status")).text

    assert "zonder TLS naar web" in status
    assert "zonder TLS naar web" in overview
    assert "/admin/settings#address-heading" in overview


async def test_the_overview_has_one_proxy_row_for_the_worst_problem(site: Site) -> None:
    _settings(site, forwarded_allow_ips="10.0.0.5")
    await _save(site, finding=AddressFinding.OTHER_INSTANCE)
    await site.login_as(SUPER)

    finding = (await site.client.get("/admin/overview/w/status")).text
    _app(site).state.proxy_watch.observe("172.18.0.1")
    untrusted = (await site.client.get("/admin/overview/w/status")).text

    assert finding.count("Proxy</") == 1
    assert "Rondgang naar het publieke adres" in finding
    assert "zonder TLS naar web" not in finding
    assert untrusted.count("Proxy</") == 1
    assert "FORWARDED_ALLOW_IPS niet noemt" in untrusted


async def test_a_healthy_round_trip_adds_no_row(site: Site) -> None:
    _settings(site)
    await _save(site)
    await site.login_as(SUPER)

    assert "Proxy</" not in (await site.client.get("/admin/overview/w/status")).text


async def test_the_login_redirect_uri_is_the_public_address_whatever_the_host_header_says(
    site: Site,
) -> None:
    _settings(site)

    await site.client.get("/admin/login", headers={"host": "evil.example"})

    assert site.idp.redirect_uris == [f"{PUBLIC}/admin/callback"]
