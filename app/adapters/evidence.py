"""Builders for the evidence report: turn what the application already measures into sections.

``python -m app.cli evidence`` (WP-C2) runs the existing checks and readers (``oidc-check``,
``address-check``, ``tls-check``, the retention overview, the log-destination status, the
key-rotation counters, the settings report) and hands their results to the functions here.
Nothing here measures anything itself, so there is no second implementation of a check
(ADR 0005); each builder chooses which short facts of a result become rows.

What may appear in a row is decided here, once:

* settings: the variable, class and source; the value only for numbers, switches and choices;
  an address as ``scheme://host``; a secret, a path or free text only as ``set`` or ``not set``;
* addresses: ``scheme://host[:port]``, never credentials, path or query (``host_of``);
* log destinations and organisations: names, kinds and counters, never a credential or a
  stored error text;
* a failing collector: the exception's class name, never its message.
"""

import re
from collections.abc import Iterable, Sequence
from datetime import datetime

from app.adapters.diagnostics import CheckResult, passed
from app.adapters.oidc_check import OidcInspection
from app.adapters.settings_report import SettingState
from app.config import Settings
from app.domain.channel_settings import ChannelSettings, SmsSettings, SmtpSettings
from app.domain.evidence import Row, Section, Status, host_of
from app.domain.key_rotation import RotationStatus
from app.domain.log_destinations import DestinationStatus, LogDestination
from app.domain.public_address import AddressCheck
from app.domain.retention import RetentionOverview
from app.domain.settings_registry import Kind
from app.domain.tls import CertificateCheck

UNKNOWN = "unknown"
_USERINFO = re.compile(r"(?<=//)[^/\s@]+@")
"""``user:password@`` right after ``//``: removed from any text copied out of a check."""
RELEASE_API = "https://api.github.com"
"""The release list the worker reads; fixed in the code (``app.adapters.releases``)."""


def collector_failed(key: str, title: str, error: BaseException) -> Section:
    """Return the section for a collector that raised: its class name only, never the message."""
    return Section(key, title, Status.FAILED, (Row("collector failed", type(error).__name__),))


def version_section(settings: Settings) -> Section:
    """Return the release, commit and image digest, with ``unknown`` where the host gave none."""
    rows = (
        Row("version", settings.app_version or "none (no release)"),
        Row("commit", settings.app_commit or UNKNOWN),
        Row("image digest", settings.app_image_digest or UNKNOWN),
        Row("environment", settings.environment),
    )
    return Section("version", "Version", Status.INFO, rows)


def oidc_section(
    inspection: OidcInspection | None, settings: Settings, issuer_pinned: str | None
) -> Section:
    """Return the outcome of ``oidc-check``: every finding, the pinned issuer and the lists.

    Args:
        inspection: The check's result; ``None`` when ``OIDC_DISCOVERY_URL`` is not set.
        settings: The settings, for the discovery host.
        issuer_pinned: The issuer the login compares with (exact), as ``OidcTarget.issuer``.
    """
    if inspection is None or settings.oidc_discovery_url is None:
        return Section(
            "oidc", "Identity provider (oidc-check)", Status.NOT_CHECKED,
            (Row("OIDC_DISCOVERY_URL", "not set: the admin portal is disabled"),),
        )  # fmt: skip
    rows = [
        Row("discovery host", host_of(settings.oidc_discovery_url)),
        Row("issuer pinned (compared exactly)", _scrub(issuer_pinned or UNKNOWN)),
        Row("OIDC_ISSUER", "set" if settings.oidc_issuer else "not set (derived from discovery)"),
        *_check_rows(inspection.results),
        *(Row(key, ", ".join(values) or "(none)") for key, values in inspection.announced.items()),
    ]
    status = Status.OK if passed(inspection.results) else Status.FAILED
    return Section("oidc", "Identity provider (oidc-check)", status, tuple(rows))


def tls_section(check: CertificateCheck, now: datetime) -> Section:
    """Return the outcome of ``tls-check``: the finding and the public certificate's facts."""
    rows = [
        Row("host", check.host),
        Row("finding", check.finding.value),
        Row("trust", check.trust.value if check.trust else "-"),
        Row("error", check.error or "-"),
    ]
    if (certificate := check.certificate) is not None:
        rows += [
            Row("subject", certificate.subject),
            Row("issuer", certificate.issuer),
            Row("names", ", ".join(certificate.names) or "-"),
            Row("valid from", certificate.not_before.isoformat()),
            Row("valid until", f"{certificate.not_after.isoformat()}"),
            Row("days left", str(certificate.days_left(now))),
        ]
    return Section("tls", "Certificate (tls-check)", _verdict(check.ok), tuple(rows))


def address_section(checks: Sequence[AddressCheck] | None) -> Section:
    """Return the outcome of ``address-check``; ``None`` means the probe is switched off."""
    title = "Public address (address-check)"
    if checks is None:
        row = Row("PUBLIC_ADDRESS_CHECK_ENABLED", "false: the probe is switched off here")
        return Section("address", title, Status.NOT_CHECKED, (row,))
    rows = [
        Row(
            f"{check.kind.value} {host_of(check.url)}",
            ", ".join(
                [check.finding.value]
                + ([f"scheme {check.scheme}"] if check.scheme else [])
                + ([f"forwarded {check.forwarded.value}"] if check.forwarded else [])
                + ([f"error {check.error}"] if check.error else [])
            ),
        )
        for check in checks
    ]
    return Section("address", title, _verdict(all(c.ok for c in checks)), tuple(rows))


def hosts_section(
    settings: Settings,
    destinations: Iterable[LogDestination],
    providers: Iterable[tuple[str, str]],
    channels: Iterable[tuple[str, ChannelSettings]],
) -> Section:
    """Return the external hosts this installation is configured to call or be called from.

    Args:
        settings: The effective settings.
        destinations: The configured log destinations.
        providers: ``(organisation slug, discovery address)`` of each organisation provider.
        channels: ``(organisation slug, settings)`` of each organisation's mail and SMS.
    """
    rows = [
        Row("public address (PUBLIC_BASE_URL)", host_of(settings.public_base_url)),
        Row("webhook address", host_of(settings.webhook_base)),
        Row("video (WEBEX_API_BASE_URL)", host_of(settings.webex_api_base_url)),
        Row("contact center (WXCC_API_BASE_URL)", _optional_host(settings.wxcc_api_base_url)),
        Row("identity provider, platform", _optional_host(settings.oidc_discovery_url)),
        Row("mail fallback (SMTP_HOST)", settings.smtp_host or "not set"),
        Row(
            "release list",
            RELEASE_API if settings.update_repository else "not read (UPDATE_REPOSITORY not set)",
        ),
    ]
    rows += [
        Row(f"identity provider, organisation {slug}", host_of(url)) for slug, url in providers
    ]
    rows += [_channel_row(slug, found) for slug, found in channels]
    rows += [
        Row(f"log destination {d.name} ({d.kind.value})", host_of(d.address)) for d in destinations
    ]
    return Section("hosts", "Configured external hosts", Status.INFO, tuple(rows))


def logs_section(
    settings: Settings,
    destinations: Sequence[LogDestination],
    status: Sequence[DestinationStatus],
) -> Section:
    """Return the log destinations and what each service reports about them (ADR 0085)."""
    rows = [
        Row("shipping kill switch (LOG_SHIPPING_ENABLED)", _flag(settings.log_shipping_enabled))
    ]
    failing = False
    for destination in destinations:
        reports = [s for s in status if s.destination_id == destination.id]
        rows.append(
            Row(
                f"{destination.name} ({destination.kind.value})",
                f"{host_of(destination.address)}; {'enabled' if destination.enabled else 'off'}; "
                f"min level {destination.min_level}; credential "
                f"{'set' if destination.secret_set else 'not set'}; tested "
                f"{destination.tested_at.isoformat() if destination.tested_at else 'never'}",
            )
        )
        for report in sorted(reports, key=lambda r: r.service):
            failing = failing or report.state.value == "failing"
            rows.append(
                Row(
                    f"  {destination.name} on {report.service}",
                    f"{report.state.value}; sent {report.sent}, dropped {report.dropped}, "
                    f"failed {report.failed}, queued {report.queued}; last ok "
                    f"{report.last_ok_at.isoformat() if report.last_ok_at else 'never'}",
                )
            )
    if not destinations:
        rows.append(Row("destinations", "none configured: logs stay on the host (stdout)"))
    return Section(
        "logs", "Log destinations", Status.WARNING if failing else Status.INFO, tuple(rows)
    )


def retention_section(overview: RetentionOverview) -> Section:
    """Return the counts of the retention overview: totals and per group, never a booking."""
    rows = [
        Row("checked at", overview.checked_at.isoformat()),
        Row("groups", str(len(overview.groups))),
        Row("bookings holding personal data", str(sum(g.holding for g in overview.groups))),
        Row("overdue (past the purge moment)", str(overview.overdue)),
        Row("failed purge jobs", str(overview.failed_purges)),
        Row(
            "last purge",
            overview.last_purged_at.isoformat() if overview.last_purged_at else "never",
        ),
    ]
    rows += [
        Row(
            f"group {g.group.organisation_slug}/{g.group.group.slug}",
            f"holding {g.holding}, overdue {g.overdue}, failed purges {g.failed_purges}",
        )
        for g in overview.groups
    ]
    return Section("retention", "Retention", _verdict(overview.in_order), tuple(rows))


def rotation_section(status: RotationStatus) -> Section:
    """Return the key-rotation counters of ADR 0094: numbers only."""
    if not status.active:
        row = Row("rotation", "none under way (no previous key is set)")
        return Section("rotation", "Key rotation", Status.INFO, (row,))
    rows = [
        Row("sealed records not under the current settings key", _count(status.records)),
        Row("open bookings still on the previous token key", _count(status.bookings)),
    ]
    done = status.records in (None, 0) and status.bookings in (None, 0)
    return Section("rotation", "Key rotation", Status.OK if done else Status.WARNING, tuple(rows))


def settings_section(states: Iterable[SettingState]) -> Section:
    """Return every setting with class and source; a secret only as set or not set."""
    rows = []
    for state in states:
        spec = state.spec
        note = "; outside portal bounds" if state.outside_bounds else ""
        note += "; stored portal value ignored" if state.portal_ignored else ""
        rows.append(
            Row(
                spec.env,
                f"class {spec.class_.value}; source {state.source}; "
                f"{'locked; ' if state.locked else ''}{_setting_value(state)}{note}",
            )
        )
    return Section("settings", "Settings (class, source, value)", Status.INFO, tuple(rows))


def _setting_value(state: SettingState) -> str:
    """Return what the report says about a setting's value (see the module docstring)."""
    kind = state.spec.kind
    if kind in (Kind.INT, Kind.FLOAT, Kind.BOOL, Kind.ENUM, Kind.RATE):
        return state.shown
    if kind is Kind.URL:
        return _optional_host(state.shown if state.is_set else None)
    return "set" if state.is_set else "not set"


def _scrub(text: str) -> str:
    """Remove credentials embedded in an address from ``text``."""
    return _USERINFO.sub("", text)


def _check_rows(results: Iterable[CheckResult]) -> list[Row]:
    """Return one row per finding: its verdict, status code and hint."""
    return [
        Row(
            result.name,
            f"{'OK' if result.ok else 'FAIL' if result.required else 'WARN'}"
            f"{f' (HTTP {result.status})' if result.status else ''}: {_scrub(result.hint)}",
        )
        for result in results
    ]


def _channel_row(slug: str, found: ChannelSettings) -> Row:
    """Return the server host of an organisation's mail or SMS channel."""
    config = found.config
    if isinstance(config, SmtpSettings):
        return Row(f"mail, organisation {slug}", f"{config.host}:{config.port} {config.security}")
    if isinstance(config, SmsSettings):
        return Row(f"SMS, organisation {slug}", host_of(config.api_base_url))
    return Row(f"channel {found.channel.value}, organisation {slug}", "configured")


def _optional_host(address: str | None) -> str:
    """Return the host of an address, or ``not set``."""
    return host_of(address) if address else "not set"


def _flag(value: bool) -> str:
    """Write a switch."""
    return "on" if value else "off"


def _count(value: int | None) -> str:
    """Write a counter that may not apply."""
    return "- (previous key not set)" if value is None else str(value)


def _verdict(ok: bool) -> Status:
    """Map a pass or fail to a status."""
    return Status.OK if ok else Status.FAILED
