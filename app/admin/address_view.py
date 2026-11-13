"""The *Address and proxy* part of System status (ADR 0086 point 1), and its one-line summary.

Everything shown comes from the environment (class B), the worker's latest round trip and the
web process's watch on forwarded headers; nothing here can be changed, so no permission beyond
the page's own is needed. No address of a participant is shown: only the proxy's, and only
when it is a loopback or private address.

    address_view(...)     the whole part, for the System status tab
    address_problem(...)  the one row of the status widget on the overview, or ``None``
"""

from dataclasses import dataclass

from fastapi import Request

from app.admin.pages import admin_moment
from app.admin.source_findings import SourceFinding, source_finding
from app.config import Settings
from app.domain.address_check import AddressStatus
from app.domain.proxy_watch import ProxyHeaderWatch
from app.domain.public_address import (
    CALLBACK_PATH,
    AddressCheck,
    AddressFinding,
    AddressKind,
    PeerClass,
    Shape,
    origin_of,
    shape_of,
    trusted_proxy,
    url_on,
)
from app.domain.web_transport import proxy_hop_unencrypted
from app.web.locale import RequestLocale
from app.web.webhook_sources import contact_center_networks, proxy_chain, video_networks
from app.web.widget_bundle import SCRIPT_URL_PATH

SHAPE_LABELS = {
    Shape.BUNDLED: "admin.settings.address.shape.bundled",
    Shape.OWN: "admin.settings.address.shape.own",
    Shape.NONE: "admin.settings.address.shape.none",
}
KIND_LABELS = {
    AddressKind.PUBLIC: "admin.settings.address.kind.public",
    AddressKind.WEBHOOK: "admin.settings.address.kind.webhook",
}
FINDING_LABELS = {
    AddressFinding.OK: "admin.settings.address.finding.ok",
    AddressFinding.UNREACHABLE: "admin.settings.address.finding.unreachable",
    AddressFinding.OTHER_INSTANCE: "admin.settings.address.finding.other_instance",
    AddressFinding.NOT_HTTPS: "admin.settings.address.finding.not_https",
    AddressFinding.PROXY_NOT_TRUSTED: "admin.settings.address.finding.proxy_not_trusted",
    AddressFinding.NO_PROXY_HEADERS: "admin.settings.address.finding.no_proxy_headers",
    AddressFinding.REDIRECTED: "admin.settings.address.finding.redirected",
}
ERROR_LABELS = {
    "timeout": "admin.settings.address.error.timeout",
    "unreachable": "admin.settings.address.error.unreachable",
    "tls": "admin.settings.address.error.tls",
    "unreadable": "admin.settings.address.error.unreadable",
}
PEER_LABELS = {
    PeerClass.LOOPBACK: "admin.settings.address.peer.loopback",
    PeerClass.PRIVATE: "admin.settings.address.peer.private",
    PeerClass.PUBLIC: "admin.settings.address.peer.public",
}
HOP_TEXT = "admin.overview.widget.status.proxy.plaintext"
"""The one message about an unencrypted proxy hop (ADR 0091 point 3), here and on the overview."""
_DANGER = (AddressFinding.OTHER_INSTANCE, AddressFinding.UNREACHABLE)


@dataclass(frozen=True)
class CheckView:
    """One address's latest round trip as the page shows it."""

    kind: str
    url: str
    finding: str
    chip: str
    checked_at: str
    reason: str
    seen: str


@dataclass(frozen=True)
class SourceRow:
    """The source networks of one installation-wide webhook address (ADR 0124 point 13)."""

    label: str
    finding: SourceFinding


@dataclass(frozen=True)
class AddressView:
    """The *Address and proxy* part of System status."""

    shape: str
    public: str
    webhook: str
    proxy: str
    callback: str
    widget: str
    switched_off: bool
    checks: list[CheckView]
    hop_warning: str
    untrusted: str
    sources: list[SourceRow]


async def address_view(request: Request, locale: RequestLocale, settings: Settings) -> AddressView:
    """Build the part from the settings, the stored round trips and the proxy watch."""
    _ = locale.gettext
    shape = shape_of(
        tls_proxy_bundled=settings.tls_proxy_bundled,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )
    proxies = trusted_proxy(
        settings.forwarded_allow_ips, tls_proxy_bundled=settings.tls_proxy_bundled
    )
    stored = await _current(request, settings)
    webhook = settings.webhook_base_url or ""
    return AddressView(
        _(SHAPE_LABELS[shape]),
        settings.public_base_url,
        webhook if webhook.rstrip("/") != settings.public_base_url.rstrip("/") else "",
        ", ".join(proxies),
        url_on(settings.public_base_url, CALLBACK_PATH),
        url_on(settings.public_base_url, SCRIPT_URL_PATH),
        not settings.public_address_check_enabled,
        [_check_view(request, locale, c) for c in stored.values()],
        _(HOP_TEXT) if _hop(settings) else "",
        _untrusted_text(request, locale),
        _source_rows(request, locale, settings),
    )


def _source_rows(request: Request, locale: RequestLocale, settings: Settings) -> list[SourceRow]:
    """Return where webhooks may come from, for the video platform and the contact-center default.

    An organisation's own list is on its connection card; here the installation's defaults are
    shown, with the proxy-chain faults that would make the gate refuse an address with a list.
    """
    _ = locale.gettext
    chain = proxy_chain(request)
    required = settings.webhook_source_networks_required
    return [
        SourceRow(
            _(label),
            source_finding(_, networks, own=False, required=required, chain=chain),
        )
        for label, networks in (
            ("admin.settings.address.sources.video", video_networks(settings)),
            ("admin.settings.address.sources.contact_center", contact_center_networks(settings)),
        )
    ]


async def _current(request: Request, settings: Settings) -> dict[AddressKind, AddressCheck]:
    """Return the stored round trips that are about the addresses configured now.

    A check made before ``PUBLIC_BASE_URL`` or ``WEBHOOK_BASE_URL`` changed says nothing about
    the new address, so it is left out until the worker has probed again.
    """
    status: AddressStatus = request.app.state.address_status
    wanted = {AddressKind.PUBLIC: origin_of(settings.public_base_url)}
    if settings.webhook_base_url:
        wanted[AddressKind.WEBHOOK] = origin_of(settings.webhook_base_url)
    return {k: c for k, c in (await status.latest()).items() if wanted.get(k) == c.url}


def _hop(settings: Settings) -> bool:
    """Return whether web trusts another proxy than the bundled one and serves no TLS."""
    return proxy_hop_unencrypted(settings.forwarded_allow_ips, web_tls=settings.web_tls_enabled)


def _check_view(request: Request, locale: RequestLocale, check: AddressCheck) -> CheckView:
    """Describe one stored round trip in words."""
    _ = locale.gettext
    reason = ""
    if check.error:
        label = ERROR_LABELS.get(check.error)
        if label is not None:
            reason = _(label)
        elif check.error.startswith("http_"):
            reason = _("admin.settings.address.error.http", status=check.error[5:])
    seen = (
        _("admin.settings.address.seen", scheme=check.scheme, forwarded=check.forwarded.value)
        if check.scheme and check.forwarded
        else ""
    )
    chip = "success" if check.ok else "danger" if check.finding in _DANGER else "warning"
    return CheckView(
        _(KIND_LABELS[check.kind]),
        check.url,
        _(FINDING_LABELS[check.finding]),
        chip,
        admin_moment(request, locale, check.checked_at),
        reason,
        seen,
    )


def _untrusted_text(request: Request, locale: RequestLocale) -> str:
    """Return what the web process saw of forwarded headers it did not honour, or ``""``."""
    watch: ProxyHeaderWatch | None = getattr(request.app.state, "proxy_watch", None)
    sighting = watch.sighting() if watch else None
    if sighting is None:
        return ""
    _ = locale.gettext
    where = (
        _("admin.settings.address.untrusted.from", peer=sighting.peer)
        if sighting.peer
        else _("admin.settings.address.untrusted.class", peer=_(PEER_LABELS[sighting.peer_class]))
    )
    return _(
        "admin.settings.address.untrusted",
        count=sighting.count,
        since=admin_moment(request, locale, sighting.first_seen),
        where=where,
    )


async def address_problem(
    request: Request, locale: RequestLocale, settings: Settings
) -> tuple[str, str] | None:
    """Return the words and tone of the overview's one *Proxy* row, or ``None`` without a problem.

    The most serious of: forwarded headers that were ignored, a round trip with a finding, an
    unencrypted proxy hop. The detail is on System status (*Address and proxy*).
    """
    _ = locale.gettext
    if _untrusted_text(request, locale):
        return _("admin.overview.widget.status.proxy.untrusted"), "danger"
    stored = await _current(request, settings)
    failing = [c for c in stored.values() if not c.ok]
    if failing and settings.public_address_check_enabled:
        finding = _(FINDING_LABELS[failing[0].finding])
        return (
            _("admin.overview.widget.status.proxy.finding", finding=finding),
            "danger" if failing[0].finding in _DANGER else "warning",
        )
    if _hop(settings):
        return _(HOP_TEXT), "warning"
    return None
