"""Command-line entry point for operators and demos.

Usage:
    ``python -m app.cli seed-demo``     create the demo organisations and groups
    ``python -m app.cli seed-closures``  add the closure examples (e2e pages); not in production
    ``python -m app.cli authz-matrix``  print the authorisation matrix (docs/authorization.md)
    ``python -m app.cli admin-map``  print the admin structure (docs/admin-map.md, ADR 0047)
    ``python -m app.cli register-webhooks``  register Webex webhooks and the WxCC subscription
    ``python -m app.cli wxcc-check``  probe every Webex/WxCC call; status and missing scope
    ``python -m app.cli wxcc-config-check``  compare WxCC's routing configuration per group
    ``python -m app.cli version``  the release and commit this image was built from (ADR 0052)
    ``python -m app.cli client-pseudonym ADDRESS``  the pseudonym security events use for an
        IP address the security team already has (ADR 0053); there is no way back
    ``python -m app.cli settings list``  every setting with its class, source and lock (ADR 0042)
    ``python -m app.cli settings show KEY``  one setting in detail; secrets only set/not set
    ``python -m app.cli settings-doc``  print the settings reference (docs/settings.md)
    ``python -m app.cli init [--out PATH]``  generate the bootstrap keys (secrets/secrets.env)

Host updater (ADR 0029; run inside the ``web`` container by ``scripts/updater``):
    ``python -m app.cli update-claim``  claim the waiting request: ``id action target from``
    ``python -m app.cli update-progress ID STEP``  record the step it starts (ADR 0048)
    ``python -m app.cli update-report ID STATUS [REASON]``  record the outcome
    ``python -m app.cli backup-db [NAME] [--prune]``  back up the database, delete old backups
    ``python -m app.cli restore-db NAME``  put a backup back (application stopped)
    ``python -m app.cli maintenance on|off|status [--reason R] [--by host-updater]``
        maintenance mode of the whole installation (ADR 0038)
    ``python -m app.cli webhooks-list``  list every registration: current, stale or foreign
    ``python -m app.cli webhooks-reset [--yes] [--reregister]``  delete stale registrations
    ``python -m app.cli webhooks-health``  token expiry and a signed delivery self-test

Settings (ADR 0042 §10; changes audited as ``system:cli``):
    ``python -m app.cli settings list|show KEY``  every setting with class, source and lock
    ``python -m app.cli settings set KEY VALUE [--hours N]``  change a class C setting
    ``python -m app.cli settings reset KEY|--all-class-c``  back to the default

When nobody can log in (ADR 0042 §8, §10; host access is the authorisation):
    ``python -m app.cli oidc-check``  check the login configuration and print the claim mapping
    ``python -m app.cli tls-check [--host H] [--port P]``  the certificate of the public address
        (ADR 0064): stored for System status; with ``--host`` another server, stored nowhere
    ``python -m app.cli closure start (--group SLUG | --organisation SLUG) --notice-nl TEXT
    --notice-en TEXT [--end ISO]``  emergency closure (ADR 0024), audited as ``system:cli``
    ``python -m app.cli closure lift (--group SLUG | --organisation SLUG)``  end it now

Run them inside the ``web`` container (``docker compose exec web ...``): they share its
database (one token store) and its secrets. The webhook commands store their last result for
the admin portal's integrations page.
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.adapters.bootstrap import (
    DEFAULT_PATH,
    InitRefusedError,
    remaining_variables,
    write_secrets,
)
from app.adapters.clock import SystemClock
from app.adapters.db.backup import (
    backup_database,
    database_path,
    prune_backups,
    restore_database,
)
from app.adapters.db.configuration_package import (
    SqliteConfigurationTransfer,
    environment_values,
    portable_environment,
)
from app.adapters.db.engine import create_engine
from app.adapters.db.reports import HEALTH_REPORT, REGISTRATIONS_REPORT, ReportStore
from app.adapters.db.seed import DEMO_SLUG, seed_closure_examples, seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.diagnostics import CheckResult, format_report, passed, run_checks
from app.adapters.integrations import build_integrations, secret_box
from app.adapters.oidc import claim_mapping, login_claims
from app.adapters.oidc_check import OidcTarget, check_oidc
from app.adapters.registrations import (
    Expected,
    Listing,
    delete_stale,
    format_listing,
    list_registrations,
    listing_to_report,
)
from app.adapters.releases import update_service
from app.adapters.service_app_check import service_app_check
from app.adapters.settings_report import (
    combination_problem,
    describe,
    format_list,
    format_one,
    locked_keys,
)
from app.adapters.tls_probe import SslCertificateProbe
from app.adapters.webex_auth import AuthorizedClient, TokenHealth, WebexTokens
from app.adapters.webhook_registration import register_webex, register_wxcc
from app.adapters.webhook_selftest import deliveries_to_report, delivery_checks, self_test
from app.adapters.wxcc_config import GroupRouting, check_groups, fetch_config, region_check
from app.config import Settings, environment
from app.domain.admin import AdminService
from app.domain.authorization import Grant, Principal, Role, RoleCatalogue, Scope
from app.domain.client_pseudonym import UNKNOWN_CLIENT, ClientPseudonyms
from app.domain.closure_service import ClosureInput, ClosureService, GroupOutcome
from app.domain.configuration_package import COUNTED_TABLES, PackageSummary
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.entitlements import ClaimMode, resolve
from app.domain.errors import DomainError
from app.domain.maintenance import MaintenanceReason, MaintenanceStatus
from app.domain.maintenance_service import MaintenanceService
from app.domain.platform_settings import EMPTY, PlatformSettingsStore, PortalValues
from app.domain.ports import CertificateProbe, IdentityError, IntegrationError
from app.domain.scheduling import Group
from app.domain.services import BookingService
from app.domain.settings_registry import lookup
from app.domain.settings_service import SettingsService
from app.domain.system_log import SystemLog
from app.domain.tls import Address, CertificateCheck
from app.domain.tls_check import TlsWatch
from app.domain.update_service import UpdateService
from app.domain.updates import OUTCOMES, REPORTED_STEPS
from app.i18n import Translator, load_catalogues
from app.keys import client_pseudonym_key, management_token_key
from app.web import navigation
from app.web.authz_report import render_markdown
from app.web.settings_doc import render_markdown as render_settings_doc


async def _seed_demo(settings: Settings) -> str:
    """Seed the demo group and return a one-line report."""
    engine = create_engine(settings.database_url)
    try:
        created = await seed_demo(engine)
    finally:
        await engine.dispose()
    state = "created" if created else "already present"
    return f"Demo group {state}: {settings.public_base_url.rstrip('/')}/g/{DEMO_SLUG}\n"


async def _seed_closures(settings: Settings) -> tuple[str, bool]:
    """Seed the demo and its closure examples; report their paths as JSON (ADR 0037).

    Refused in production: the examples include a booking of a made-up participant.
    """
    if settings.environment == "prod":
        return "seed-closures adds a made-up booking; it runs only outside production\n", False
    engine = create_engine(settings.database_url)
    try:
        await seed_demo(engine)
        examples = await seed_closure_examples(
            engine, SystemClock().now(), management_token_key(settings)
        )
    finally:
        await engine.dispose()
    return json.dumps(dataclasses.asdict(examples), indent=2) + "\n", True


@asynccontextmanager
async def _service_app(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, WebexTokens | None]]:
    """Yield an HTTP client and the Service App's tokens (``None`` when not configured)."""
    engine = create_engine(settings.database_url)
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as http:
            integrations = build_integrations(settings, engine, http, SystemClock())
            yield http, integrations.tokens
    finally:
        await engine.dispose()


async def _groups(settings: Settings) -> list[Group]:
    """Return every group (operator commands see all organisations)."""
    engine = create_engine(settings.database_url)
    try:
        async with SqlUnitOfWork(engine).read() as tx:
            return [summary.group for summary in await tx.list_groups(Scope(everything=True))]
    finally:
        await engine.dispose()


def _hosts(groups: list[Group]) -> list[str]:
    """Return the groups' video host accounts (one Webex webhook set per account)."""
    return [g.video_host_email for g in groups if g.video_host_email]


async def _store(settings: Settings, name: str, body: dict[str, object]) -> None:
    """Store a check report for the admin portal."""
    engine = create_engine(settings.database_url)
    try:
        await ReportStore(engine).put(name, body, SystemClock().now())
    finally:
        await engine.dispose()


NOT_CONFIGURED = (
    "Webex is not configured (WEBEX_CLIENT_ID and WEBEX_WEBHOOK_SECRET); nothing to do.\n"
)


async def _register_webhooks(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Register the webhooks the configured integrations need; return a report."""
    base = settings.webhook_base
    if not settings.video_configured or settings.webex_webhook_secret is None:
        return NOT_CONFIGURED
    if not base.startswith("https://"):
        return (
            "WEBHOOK_BASE_URL (or PUBLIC_BASE_URL) must be https: Webex and WxCC only call "
            "public https URLs.\n"
        )
    async with _service_app(settings, transport) as (http, tokens):
        if tokens is None:  # pragma: no cover - video_configured implies tokens
            return "Webex is not configured; nothing to do.\n"
        client = AuthorizedClient(http, tokens)
        lines = await register_webex(
            client,
            settings.webex_api_base_url,
            f"{base}/webhooks/webex",
            settings.webex_webhook_secret.get_secret_value(),
            hosts=_hosts(await _groups(settings)),
        )
        if settings.wxcc_api_base_url and settings.wxcc_webhook_secret is not None:
            lines.append(
                await register_wxcc(
                    client,
                    settings.wxcc_api_base_url,
                    f"{base}/webhooks/wxcc",
                    settings.wxcc_webhook_secret.get_secret_value(),
                )
            )
    return "".join(f"{line}\n" for line in lines)


async def _wxcc_check(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, bool]:
    """Probe every Webex and WxCC call; return the report and whether all passed."""
    if not settings.video_configured:
        return (
            "Webex is not configured (WEBEX_CLIENT_ID, WEBEX_CLIENT_SECRET, "
            "WEBEX_REFRESH_TOKEN); nothing to check.\n",
            False,
        )
    async with _service_app(settings, transport) as (http, tokens):
        if tokens is None:  # pragma: no cover - video_configured implies tokens
            return "Webex is not configured; nothing to check.\n", False
        results = await service_app_check(
            http, tokens, settings.webex_api_base_url, settings.wxcc_api_base_url, SystemClock()
        )
    report = format_report(results)
    if not settings.wxcc_api_base_url:
        report += "WXCC_API_BASE_URL is not set: WxCC was not checked.\n"
    return report, passed(results)


async def _wxcc_config_check(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, bool]:
    """Compare WxCC's work item routing with every group's WxCC fields; report and verdict."""
    if not settings.dispatch_configured or not settings.wxcc_api_base_url:
        return (
            "WxCC is not configured (Service App and WXCC_API_BASE_URL); nothing to check.\n",
            False,
        )
    async with _service_app(settings, transport) as (http, tokens):
        groups = [
            GroupRouting(g.slug, g.wxcc_channel, g.wxcc_business_address, g.routing_value)
            for g in await _groups(settings)
            if g.wxcc_channel and g.wxcc_business_address
        ]
        if not groups:
            return "No group has a WxCC channel and business address; nothing to check.\n", False
        if tokens is None:  # pragma: no cover - dispatch_configured implies tokens
            return "Webex is not configured; nothing to check.\n", False
        try:
            token = await tokens.access_token()
            region, host, org = await region_check(http, token, settings.wxcc_api_base_url)
            results: list[CheckResult] = [region]
            if host and org:
                results += check_groups(groups, await fetch_config(http, token, host, org))
        except IntegrationError as error:
            results = [CheckResult("WxCC configuration", None, False, f"failed: {error.code}")]
    return format_report(results), passed(results)


@asynccontextmanager
async def _updates(settings: Settings) -> AsyncIterator[UpdateService]:
    """Yield the update service on the configured database (no release lookups)."""
    engine = create_engine(settings.database_url)
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            local = settings.model_copy(update={"update_repository": None})
            yield update_service(local, SqlUnitOfWork(engine), SystemClock(), http)
    finally:
        await engine.dispose()


def _version(settings: Settings) -> str:
    """Return the release and commit this image was built from (ADR 0052)."""
    release = settings.app_version or "none (no release)"
    return f"version {release}\ncommit {settings.app_commit or 'unknown'}\n"


async def _update_claim(settings: Settings) -> str:
    """Claim the waiting update request; return ``id action target from`` or nothing.

    Every field is validated by the domain (an integer, an action, two versions), so the
    updater can split the line without quoting issues.
    """
    async with _updates(settings) as updates:
        started = await updates.claim()
    if started is None:
        return ""
    return f"{started.id} {started.action} {started.target} {started.from_version}\n"


async def _update_progress(settings: Settings, request_id: int, step: str) -> tuple[str, int]:
    """Record the step a running request has reached; return the stored step and an exit code."""
    steps = {s.value: s for s in REPORTED_STEPS}
    if step not in steps:
        return f"unknown step: {step}\n", 2
    try:
        async with _updates(settings) as updates:
            moved = await updates.report_step(request_id, steps[step])
    except DomainError as error:
        return f"refused: {error}\n", 1
    return f"{moved.step}\n", 0


async def _update_report(
    settings: Settings, request_id: int, status: str, reason: str
) -> tuple[str, int]:
    """Record the outcome of a running request; return the stored status and an exit code."""
    outcomes = {s.value: s for s in OUTCOMES}
    if status not in outcomes:
        return f"unknown status: {status}\n", 2
    try:
        async with _updates(settings) as updates:
            ended = await updates.report(request_id, outcomes[status], reason)
    except DomainError as error:
        return f"refused: {error}\n", 1
    return f"{ended.status}\n", 0


HOST_ACTORS = ("host-cli", "host-updater")
"""Who a maintenance switch from the host is audited as; a fixed list, never free text."""


def _maintenance_line(status: MaintenanceStatus) -> str:
    """Return ``on``/``off`` with the source and the stored details, for scripts and people."""
    state = status.state
    if not status.active:
        return "off\n"
    parts = ["on"]
    if status.environment:
        parts.append("environment")
    if state.on:
        since = state.since.isoformat() if state.since else "?"
        parts += [f"since={since}", f"by={state.by}"]
        if state.reason:
            parts.append(f"reason={state.reason}")
    return " ".join(parts) + "\n"


async def _maintenance(settings: Settings, action: str, reason: str, actor: str) -> str:
    """Switch maintenance on or off, or report it; return the resulting state as one line."""
    engine = create_engine(settings.database_url)
    try:
        uow, clock = SqlUnitOfWork(engine), SystemClock()
        service = MaintenanceService(
            uow,
            clock,
            AdminService(uow, clock),
            environment=settings.maintenance_mode,
            cache=timedelta(0),
        )
        if action == "on":
            await service.switch_on_host(actor, MaintenanceReason(reason))
        elif action == "off":
            await service.switch_off_host(actor)
        return _maintenance_line(await service.stored())
    finally:
        await engine.dispose()


async def _tls_check(
    settings: Settings,
    host: str | None = None,
    port: int | None = None,
    probe: CertificateProbe | None = None,
) -> tuple[str, bool]:
    """Probe the certificate of the public address (stored) or of ``host`` (not stored).

    Returns the report and whether the finding is ``ok``. The thresholds are the environment's
    or the defaults; the worker applies the portal's.
    """
    engine = create_engine(settings.database_url)
    try:
        uow, clock = SqlUnitOfWork(engine), SystemClock()
        watch = TlsWatch(
            uow,
            clock,
            probe or SslCertificateProbe(),
            SystemLog(uow, clock),
            public_base_url=settings.public_base_url,
            interval=timedelta(hours=settings.tls_check_interval_hours),
            warn_within=timedelta(days=settings.tls_expiry_warning_days),
            repeat=timedelta(hours=settings.tls_alert_repeat_hours),
        )
        if host:
            check = await watch.probe(Address(host, port or 443))
        else:
            check = await watch.check()
        return _tls_lines(check, clock.now()), check.ok
    finally:
        await engine.dispose()


def _tls_lines(check: CertificateCheck, now: datetime) -> str:
    """Write a check as ``name  value`` lines."""
    certificate = check.certificate
    rows = [
        ("host", check.host),
        ("finding", check.finding.value),
        ("trust", check.trust.value if check.trust else "-"),
        ("error", check.error or "-"),
    ]
    if certificate is not None:
        rows += [
            ("subject", certificate.subject),
            ("issuer", certificate.issuer),
            ("names", ", ".join(certificate.names) or "-"),
            ("valid from", certificate.not_before.isoformat()),
            (
                "valid until",
                f"{certificate.not_after.isoformat()} ({certificate.days_left(now)} days)",
            ),
        ]
    return "".join(f"{name:<12}{value}\n" for name, value in rows)


def _backup_dir(settings: Settings) -> tuple[Path, Path]:
    """Return the database file and its backup directory (``data/backups``)."""
    database = database_path(settings.database_url)
    return database, database.parent / "backups"


def _backup(settings: Settings, name: str | None, *, prune: bool) -> str:
    """Back up the database as ``name`` (if given) and delete expired backups (if asked)."""
    database, directory = _backup_dir(settings)
    lines = []
    if name is not None:
        backup_database(database, directory, name)
        lines.append(f"backup written: {name}\n")
    if prune:
        lines.append(f"old backups deleted: {prune_backups(directory, datetime.now(UTC))}\n")
    return "".join(lines)


def _restore(settings: Settings, name: str) -> str:
    """Put backup ``name`` back; the web and worker containers must be stopped."""
    database, directory = _backup_dir(settings)
    restore_database(database, directory, name)
    return f"restored {name}\n"


async def _listing(settings: Settings, client: AuthorizedClient) -> Listing:
    """List and classify every registration and store the result for the admin portal."""
    base = settings.webhook_base
    wxcc_wired = settings.wxcc_api_base_url and settings.wxcc_webhook_secret is not None
    expected = Expected(
        webex_target=f"{base}/webhooks/webex",
        wxcc_target=f"{base}/webhooks/wxcc" if wxcc_wired else None,
        hosts=tuple(_hosts(await _groups(settings))),
    )
    listing = await list_registrations(
        client, settings.webex_api_base_url, settings.wxcc_api_base_url, expected
    )
    await _store(settings, REGISTRATIONS_REPORT, listing_to_report(listing) | {"base": base})
    return listing


async def _webhooks_list(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, bool]:
    """List every registration; the verdict is whether all are current and none is missing."""
    if not settings.video_configured or settings.webex_webhook_secret is None:
        return NOT_CONFIGURED, False
    async with _service_app(settings, transport) as (http, tokens):
        if tokens is None:  # pragma: no cover - video_configured implies tokens
            return NOT_CONFIGURED, False
        listing = await _listing(settings, AuthorizedClient(http, tokens))
    header = f"Compared with WEBHOOK_BASE_URL {settings.webhook_base}\n"
    ok = listing.complete and not listing.stale and not listing.missing
    return header + format_listing(listing), ok


async def _webhooks_reset(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    confirmed: bool,
    reregister: bool = False,
) -> tuple[str, bool]:
    """Delete stale registrations (a dry run unless ``confirmed``), then maybe register anew.

    Foreign and current registrations are never deleted. The verdict is whether nothing was
    refused or failed.
    """
    if not settings.video_configured or settings.webex_webhook_secret is None:
        return NOT_CONFIGURED, False
    async with _service_app(settings, transport) as (http, tokens):
        if tokens is None:  # pragma: no cover - video_configured implies tokens
            return NOT_CONFIGURED, False
        client = AuthorizedClient(http, tokens)
        listing = await _listing(settings, client)
        lines = [f"{len(listing.stale)} stale registration(s)."]
        lines += [f"{e.platform} listing: refused ({e.code})" for e in listing.errors]
        lines += await delete_stale(
            client,
            settings.webex_api_base_url,
            settings.wxcc_api_base_url,
            listing,
            confirmed=confirmed,
        )
        if confirmed:
            await _listing(settings, client)
    ok = not any(": refused" in line or ": failed" in line for line in lines)
    if not confirmed:
        lines.append("Dry run: nothing deleted. Run again with --yes to delete.")
        if reregister:
            lines.append("--reregister runs register-webhooks only together with --yes.")
    report = "".join(f"{line}\n" for line in lines)
    if confirmed and reregister:
        registered = await _register_webhooks(settings, transport)
        ok = ok and ": refused" not in registered
        report += registered
    return report, ok


async def _webhooks_health(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, bool]:
    """Report token health and send signed test events through ``WEBHOOK_BASE_URL``."""
    base = settings.webhook_base
    results: list[CheckResult] = []
    if not base.startswith("https://"):
        results.append(
            CheckResult("WEBHOOK_BASE_URL", None, False, "not https: Webex and WxCC cannot deliver")
        )
    clock = SystemClock()
    async with _service_app(settings, transport) as (http, tokens):
        if tokens is not None:
            results += await run_checks(http, tokens, [], clock)
            results += _token_expiry(await tokens.health())
        webex, wxcc = settings.webex_webhook_secret, settings.wxcc_webhook_secret
        deliveries = await self_test(
            http,
            base,
            webex_secret=webex.get_secret_value() if webex else None,
            wxcc_secret=wxcc.get_secret_value() if wxcc else None,
            now=clock.now(),
        )
    await _store(settings, HEALTH_REPORT, deliveries_to_report(deliveries) | {"base": base})
    results += delivery_checks(deliveries)
    if not deliveries:
        results.append(
            CheckResult("delivery self-test", None, False, "no webhook secret is configured")
        )
    return f"Delivery through {base}\n" + format_report(results), passed(results)


def _token_expiry(health: TokenHealth) -> list[CheckResult]:
    """Return when the access and refresh tokens expire (``WARN`` within 14 days)."""
    if health.access_expires_at is None or health.refresh_expires_at is None:
        return []
    refresh = health.refresh_expires_at.strftime("%Y-%m-%d %H:%M UTC")
    return [
        CheckResult(
            "access token",
            None,
            True,
            f"expires {health.access_expires_at.strftime('%Y-%m-%d %H:%M UTC')}",
        ),
        CheckResult(
            "refresh token",
            None,
            not health.refresh_expiring_soon,
            f"expires {refresh}"
            + (": generate new tokens soon (runbook)" if health.refresh_expiring_soon else ""),
            required=False,
        ),
    ]


# --- when nobody can log in (ADR 0042 §8, §10) ------------------------------------------------

CLI_ACTOR = Principal(
    subject="system:cli",
    display_name="host CLI",
    grants=frozenset({Grant(Role.SUPERUSER.value)}),
)
"""Who the host commands act as: access to the host is the authorisation (ADR 0042 §10).

Used only inside this process to call the same domain services as the portal, so their
rules and audit apply unchanged; it never becomes a session, a user or a role grant.
"""

CLAIM_SETTINGS = (
    "oidc_role_claim",
    "oidc_organisation_claim",
    "oidc_group_claim",
    "oidc_role_superuser",
    "oidc_role_org_admin",
    "oidc_role_group_admin",
    "oidc_role_auditor",
    "oidc_role_agent",
    "oidc_entitlement_claim",
    "oidc_entitlement_prefix",
    "oidc_claim_mode",
)
"""The claim mapping ``oidc-check`` prints: claim names, role values and the mode, never secrets."""


async def _oidc_check(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, bool]:
    """Check the login configuration against the provider; print the effective claim mapping.

    Two reads only (discovery and key set): no login is started and no secret is printed.
    """
    if settings.oidc_discovery_url is None:
        return "OIDC_DISCOVERY_URL is not set: the admin portal is disabled.\n", False
    target = OidcTarget(
        discovery_url=settings.oidc_discovery_url,
        redirect_uri=f"{settings.public_base_url.rstrip('/')}/admin/callback",
        require_https=settings.environment == "prod",
        client_secret_set=settings.oidc_client_secret is not None,
    )
    async with httpx.AsyncClient(timeout=15.0, transport=transport) as http:
        results = await check_oidc(http, target)
    width = max(len(name) for name in CLAIM_SETTINGS) + 1
    mapping = "".join(
        f"  {name.upper():<{width}}{getattr(settings, name)}  "
        f"{'env' if name in settings.model_fields_set else 'default'}\n"
        for name in CLAIM_SETTINGS
    )
    return format_report(results) + "\nClaim mapping (value, source):\n" + mapping, passed(results)


def _read_claims(path: Path) -> object:
    """Return the sample claims of ``path`` as data, or the reason it is unusable."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return f"cannot read {path}: {type(exc).__name__}"


async def _oidc_claims(settings: Settings, sample: object) -> tuple[str, bool]:
    """Resolve ``sample`` claims without a login (ADR 0049 point 16).

    Prints the mode, every grant and every refused value with its reason, so the IdP team can
    test an attribute contract before anyone logs in. Reads the directory from the database;
    the sample is data and is never logged.
    """
    if isinstance(sample, str):
        return f"{sample}\n", False
    if not isinstance(sample, dict) or "sub" not in sample:
        return 'the claims file must be a JSON object with at least "sub"\n', False
    mapping = claim_mapping(settings.login_values, settings.oidc_entitlements_max_values)
    try:
        claims = login_claims(sample, mapping, log_lockout=False)
    except IdentityError as exc:
        return f"refused: {exc.code}\n", False
    engine = create_engine(settings.database_url)
    try:
        uow = SqlUnitOfWork(engine, secret_box(settings))
        async with uow.read() as tx:
            directory = await tx.get_directory()
            catalogue = RoleCatalogue(tuple(await tx.get_custom_roles()))
    finally:
        await engine.dispose()
    mode = ClaimMode(settings.oidc_claim_mode)
    outcome = resolve(claims, mode, settings.oidc_entitlement_prefix, directory, catalogue)
    lines = [f"mode: {mode.value}", f"subject: {claims.subject}"]
    if outcome.ambiguous:
        lines.append("refused: ambiguous_claims (two roles of one reach over several targets)")
    lines += [f"grant: {g.key}" for g in sorted(outcome.principal.grants, key=lambda g: g.key)]
    lines += [f"refused value: {r.value} ({r.reason.value})" for r in outcome.refused]
    if outcome.mismatch:
        lines.append(f"shadow mismatch: {outcome.mismatch}")
    if not outcome.principal.grants:
        lines.append("no grant: this login would hold no role")
    return "\n".join(lines) + "\n", bool(outcome.principal.grants) and not outcome.ambiguous


@asynccontextmanager
async def _closures(settings: Settings) -> AsyncIterator[ClosureService]:
    """Yield the closure service wired as in the web process (same rules, mails, audit)."""
    engine = create_engine(settings.database_url)
    try:
        uow = SqlUnitOfWork(engine, secret_box(settings))
        clock = SystemClock()
        bookings = BookingService(uow, clock, management_token_key(settings))
        yield ClosureService(uow, clock, AdminService(uow, clock), bookings)
    finally:
        await engine.dispose()


def _group_lines(verb: str, outcomes: list[GroupOutcome], suffix: str) -> str:
    """Return one line per group of an organisation-wide closure or lift."""
    return "".join(
        f"{verb} {o.slug}{suffix.format(o.bookings)}\n"
        if o.changed
        else f"skipped {o.slug}: {'already closed' if verb == 'closed' else 'not closed'}\n"
        for o in outcomes
    )


async def _closure_start(
    settings: Settings,
    *,
    group: str | None,
    organisation: str | None,
    notices: dict[str, str],
    ends_at: datetime | None,
) -> tuple[str, bool]:
    """Close a group or every group of an organisation now (ADR 0024) for an IdP outage."""
    closure = ClosureInput(starts_at=None, ends_at=ends_at, notices=notices)
    suffix = ": {} bookings on hold"
    try:
        async with _closures(settings) as service:
            if group is not None:
                count = await service.close_group(CLI_ACTOR, group, closure)
                return f"closed {group}{suffix.format(count)}\n", True
            # argparse requires one of the two; an empty slug is refused as not permitted.
            outcomes = await service.close_organisation(CLI_ACTOR, organisation or "", closure)
    except DomainError as error:
        return f"refused: {error.code}\n", False
    return _group_lines("closed", outcomes, suffix) or "no groups\n", True


async def _closure_lift(
    settings: Settings, *, group: str | None, organisation: str | None
) -> tuple[str, bool]:
    """End a closure now; bookings on hold stay on hold until an admin handles them."""
    try:
        async with _closures(settings) as service:
            if group is not None:
                await service.lift(CLI_ACTOR, group, reinstate=False)
                return f"lifted {group}\n", True
            outcomes = await service.lift_organisation(
                CLI_ACTOR, organisation or "", reinstate=False
            )
    except DomainError as error:
        return f"refused: {error.code}\n", False
    return _group_lines("lifted", outcomes, "") or "no groups\n", True


def _moment(value: str) -> datetime:
    """Parse ``--end``: ISO 8601 with an offset, so the moment is never ambiguous."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use ISO 8601, e.g. 2026-11-02T08:00+01:00") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("include the offset, e.g. 2026-11-02T08:00+01:00")
    return parsed.astimezone(UTC)


CONFIG_ACTOR = "system:cli"
"""The audit actor of the host's configuration commands (ADR 0042 §10, ADR 0089 point 6)."""


def _config_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Add ``config export|show|import|env`` (ADR 0089): the configuration package on the host."""
    config_cmd = commands.add_parser(
        "config", help="export or import a configuration package (ADR 0089)"
    )
    actions = config_cmd.add_subparsers(dest="action", required=True)
    export_cmd = actions.add_parser("export", help="write a package of this installation")
    export_cmd.add_argument("path", type=Path, help="the package file to write (mode 600)")
    show_cmd = actions.add_parser("show", help="what a package holds")
    show_cmd.add_argument("path", type=Path, help="the package file")
    import_cmd = actions.add_parser(
        "import", help="replace this installation's configuration (not in use only)"
    )
    import_cmd.add_argument("path", type=Path, help="the package file")
    env_cmd = actions.add_parser("env", help="write the package's login variables as an env file")
    env_cmd.add_argument("path", type=Path, help="the package file")
    env_cmd.add_argument(
        "--out",
        type=Path,
        required=True,
        help="the env file to write (600), or - for standard output (the installer's one-off "
        "container, whose filesystem is read-only; ADR 0089 point 8)",
    )
    for sub in (export_cmd, show_cmd, import_cmd, env_cmd):
        sub.add_argument(
            "--passphrase-file",
            type=Path,
            required=True,
            metavar="PATH",
            help="a file whose first line is the passphrase (never on the command line)",
        )


def _passphrase(path: Path) -> str:
    """Return the first line of ``path`` without its line ending."""
    with path.open(encoding="utf-8") as handle:
        return handle.readline().rstrip("\r\n")


def _write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` with mode 600, replacing an existing file."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _summary_lines(summary: PackageSummary) -> str:
    """Return a package summary as the ``show`` and ``import`` commands print it."""
    rows = [
        ("exported", summary.created_at.isoformat()),
        ("from", summary.installation or "(no installation name)"),
        ("version", summary.app_version or "(development)"),
        ("revision", summary.revision),
        *((name, str(summary.counts.get(name, 0))) for name in COUNTED_TABLES),
        ("secrets", str(summary.secrets)),
        ("environment", ", ".join(summary.environment) or "(none)"),
    ]
    return "".join(f"{name:<20}{value}\n" for name, value in rows)


async def _config(settings: Settings, args: argparse.Namespace) -> tuple[str, int]:
    """Run a ``config`` command; return the report and exit code. Never prints a secret."""
    passphrase = _passphrase(args.passphrase_file)
    engine = create_engine(settings.database_url)
    clock = SystemClock()
    transfer = SqliteConfigurationTransfer(
        database_path(settings.database_url),
        secret_box(settings),
        installation=settings.installation_name,
        app_version=settings.app_version,
        environment=lambda: portable_environment(settings),
        now=clock.now,
    )
    service = ConfigurationPackageService(
        transfer,
        clock,
        AdminService(SqlUnitOfWork(engine), clock),
        max_bytes=lambda: settings.configuration_package_max_mb * 1024 * 1024,
    )
    try:
        if args.action == "export":
            _write_private(args.path, await service.export(CONFIG_ACTOR, passphrase))
            return f"package written: {args.path}\n", 0
        package = await asyncio.to_thread(Path(args.path).read_bytes)
        if args.action == "show":
            return _summary_lines(await service.inspect(package, passphrase)), 0
        if args.action == "env":
            values = environment_values(package, passphrase, transfer.work_dir)
            lines = "".join(f"{key}={value}\n" for key, value in values.items())
            if str(args.out) == "-":
                return lines, 0
            _write_private(args.out, lines.encode())
            return f"env file written: {args.out} ({len(values)} variables)\n", 0
        summary = await service.import_(CONFIG_ACTOR, package, passphrase)
        return "configuration imported\n" + _summary_lines(summary), 0
    except DomainError as error:
        return f"Refused ({error.code.value}).\n", 2
    finally:
        await engine.dispose()


def _closure_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Add ``closure start`` and ``closure lift``, each for one group or one organisation."""
    closure = commands.add_parser("closure", help="emergency closure while nobody can log in")
    actions = closure.add_subparsers(dest="closure_action", required=True)
    start = actions.add_parser("start", help="close now")
    lift = actions.add_parser("lift", help="end the closure now")
    for action in (start, lift):
        target = action.add_mutually_exclusive_group(required=True)
        target.add_argument("--group", metavar="SLUG")
        target.add_argument("--organisation", metavar="SLUG")
    start.add_argument("--notice-nl", required=True, metavar="TEXT")
    start.add_argument("--notice-en", required=True, metavar="TEXT")
    start.add_argument("--end", type=_moment, metavar="ISO", help="default: until lifted")


def _closure(settings: Settings, args: argparse.Namespace) -> int:
    """Run ``closure start`` or ``closure lift``."""
    if args.closure_action == "start":
        notices = {"nl": args.notice_nl, "en": args.notice_en}
        outcome = _closure_start(
            settings,
            group=args.group,
            organisation=args.organisation,
            notices=notices,
            ends_at=args.end,
        )
    else:
        outcome = _closure_lift(settings, group=args.group, organisation=args.organisation)
    return _write(asyncio.run(outcome))


def _init(out: Path) -> tuple[str, bool]:
    """Generate the bootstrap keys into ``out``; list what the operator still sets (§9).

    Needs no settings: it runs before the environment is complete. Never prints a key.
    """
    try:
        write_secrets(out)
    except InitRefusedError:
        return (
            f"{out} exists; nothing written. A new SETTINGS_ENCRYPTION_KEY would make every "
            "secret stored in the database unreadable.\n",
            False,
        )
    width = max(len(spec.env) for spec in remaining_variables()) + 2
    todo = "".join(f"  {spec.env:<{width}}{spec.description}\n" for spec in remaining_variables())
    return (
        f"Wrote {out} (mode 0600): SETTINGS_ENCRYPTION_KEY, MANAGEMENT_TOKEN_KEY, "
        "SESSION_SECRET.\n"
        "Keep a copy of this file apart from the database backup.\n\n"
        "Still to set in .env or the secret store (class A and B; docs/settings.md):\n" + todo,
        True,
    )


def _settings(settings: Settings, action: str, key: str | None) -> tuple[str, int]:
    """Run ``settings list`` or ``settings show KEY``; return the report and exit code.

    The portal values come from the database; when it cannot be read, the report says so
    and shows the environment and the defaults only.
    """
    spec = lookup(key or "") if action != "list" else None
    if action != "list" and spec is None:
        return "Unknown setting; `settings list` shows every variable.\n", 2
    portal, note = asyncio.run(_portal_values(settings))
    states = describe(settings, environment(), portal)
    if spec is None:
        return note + format_list(states), 0
    return note + format_one(next(s for s in states if s.spec is spec)), 0


async def _change_setting(settings: Settings, args: argparse.Namespace) -> tuple[str, int]:
    """Run ``settings set`` or ``settings reset``; audited as ``system:cli`` (ADR 0042 §10).

    Refusals name the code and the variable, never the submitted value. The processes pick
    the change up within 10 seconds (web) or one loop (worker).
    """
    spec = lookup(args.key or "") if args.key else None
    if args.key and spec is None:
        return "Unknown setting; `settings list` shows every variable.\n", 2
    environ = environment()
    engine = create_engine(settings.database_url)
    uow = SqlUnitOfWork(engine)
    clock = SystemClock()
    service = SettingsService(
        uow,
        clock,
        AdminService(uow, clock),
        locked=lambda: locked_keys(environ),
        combination=lambda proposed: combination_problem(settings, environ, proposed),
    )
    try:
        if args.action == "set" and spec is not None:
            await service.change_host(spec.key, args.value, hours=args.hours)
            return f"{spec.env} set to {args.value.strip()}.\n", 0
        if spec is not None:
            removed = await service.reset_host(spec.key)
            return f"{spec.env} reset.\n" if removed else f"{spec.env} had no portal value.\n", 0
        return f"{await service.reset_all_host()} portal values removed.\n", 0
    except DomainError as error:
        name = spec.env if spec else "setting"
        return f"Refused ({error.code.value}): {name}.\n", 2
    finally:
        await engine.dispose()


async def _portal_values(settings: Settings) -> tuple[PortalValues, str]:
    """Read the stored portal values; on failure return none and a note naming the error type."""
    engine = create_engine(settings.database_url)
    store = PlatformSettingsStore(SqlUnitOfWork(engine))
    try:
        await store.refresh()
    except Exception as exc:  # no database, or not migrated: the environment still tells a lot
        return EMPTY, f"Portal values not read ({type(exc).__name__}); showing env and defaults.\n"
    finally:
        await engine.dispose()
    return store.current, ""


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="create the demo organisations and groups")
    commands.add_parser("seed-closures", help="add the closure examples (not in production)")
    commands.add_parser("authz-matrix", help="print the authorisation matrix as Markdown")
    commands.add_parser("admin-map", help="print the admin structure as Markdown")
    commands.add_parser("register-webhooks", help="register Webex webhooks and WxCC subscription")
    commands.add_parser("wxcc-check", help="probe the Webex and WxCC calls (status, scope hints)")
    commands.add_parser("wxcc-config-check", help="check WxCC's routing configuration per group")
    commands.add_parser("version", help="print the release and commit of this image")
    commands.add_parser("update-claim", help="claim the waiting update request (host updater)")
    progress_cmd = commands.add_parser("update-progress", help="record the step an update starts")
    progress_cmd.add_argument("request_id", type=int)
    progress_cmd.add_argument("step", choices=sorted(s.value for s in REPORTED_STEPS))
    report_cmd = commands.add_parser("update-report", help="record an update's outcome")
    report_cmd.add_argument("request_id", type=int)
    report_cmd.add_argument("status", choices=sorted(s.value for s in OUTCOMES))
    report_cmd.add_argument("reason", nargs="?", default="")
    backup_cmd = commands.add_parser("backup-db", help="back up the database (data/backups)")
    backup_cmd.add_argument("name", nargs="?")
    backup_cmd.add_argument("--prune", action="store_true", help="delete backups older than 24 h")
    restore_cmd = commands.add_parser("restore-db", help="put a backup back (app stopped)")
    restore_cmd.add_argument("name")
    maintenance_cmd = commands.add_parser(
        "maintenance", help="maintenance mode of the whole installation (ADR 0038)"
    )
    maintenance_cmd.add_argument("action", choices=("on", "off", "status"))
    maintenance_cmd.add_argument(
        "--reason", choices=sorted(r.value for r in MaintenanceReason), default="other"
    )
    maintenance_cmd.add_argument("--by", choices=HOST_ACTORS, default=HOST_ACTORS[0])
    commands.add_parser("webhooks-list", help="list registrations: current, stale or foreign")
    reset = commands.add_parser("webhooks-reset", help="delete stale registrations (dry run)")
    reset.add_argument("--yes", action="store_true", help="really delete them")
    reset.add_argument(
        "--reregister", action="store_true", help="with --yes: run register-webhooks afterwards"
    )
    commands.add_parser("webhooks-health", help="token expiry and a signed delivery self-test")
    pseudonym_cmd = commands.add_parser(
        "client-pseudonym", help="pseudonym of an IP address in security events (ADR 0053)"
    )
    pseudonym_cmd.add_argument("address")
    oidc_cmd = commands.add_parser(
        "oidc-check", help="check the login configuration (no login started)"
    )
    oidc_cmd.add_argument(
        "--claims",
        help="a JSON file with sample ID-token claims: print the grants and refused values "
        "they resolve to under the current mode and directory (ADR 0049 point 16)",
    )
    tls_cmd = commands.add_parser("tls-check", help="the certificate of the public address")
    tls_cmd.add_argument("--host", help="probe another server instead (stored nowhere)")
    tls_cmd.add_argument("--port", type=int, help="its port (default 443)")
    _closure_parser(commands)
    _config_parser(commands)
    settings_cmd = commands.add_parser("settings", help="show settings and their source")
    settings_actions = settings_cmd.add_subparsers(dest="action", required=True)
    settings_actions.add_parser("list", help="every setting: class, source, lock, value")
    show_cmd = settings_actions.add_parser("show", help="one setting in detail")
    show_cmd.add_argument("key", help="variable name, e.g. RATE_LIMIT_BOOKINGS")
    set_cmd = settings_actions.add_parser("set", help="change a class C setting (audited)")
    set_cmd.add_argument("key", help="variable name, e.g. ADMIN_SESSION_HOURS")
    set_cmd.add_argument("value", help="the new value, within the portal bounds")
    set_cmd.add_argument(
        "--hours",
        type=int,
        help="expiry of LOG_LEVEL (1-24 hours; TRACE at most TRACE_LEVEL_MAX_HOURS)",
    )
    reset_cmd = settings_actions.add_parser("reset", help="remove portal values (audited)")
    target = reset_cmd.add_mutually_exclusive_group(required=True)
    target.add_argument("key", nargs="?", help="variable name")
    target.add_argument("--all-class-c", action="store_true", help="every changeable value")
    commands.add_parser("settings-doc", help="print the settings reference as Markdown")
    init_cmd = commands.add_parser("init", help="generate the bootstrap keys (first install)")
    init_cmd.add_argument("--out", type=Path, default=DEFAULT_PATH, metavar="PATH")
    args = parser.parse_args(argv)
    if args.command == "init":
        return _write(_init(args.out))
    if args.command == "version":
        sys.stdout.write(_version(Settings()))
        return 0
    if args.command == "client-pseudonym":
        pseudonym = ClientPseudonyms(client_pseudonym_key(Settings())).of(args.address)
        sys.stdout.write(f"{pseudonym}\n")
        return 0 if pseudonym != UNKNOWN_CLIENT else 1
    if args.command == "oidc-check":
        if args.claims:
            sample = _read_claims(Path(args.claims))
            return _write(asyncio.run(_oidc_claims(Settings(), sample)))
        return _write(asyncio.run(_oidc_check(Settings())))
    if args.command == "tls-check":
        return _write(asyncio.run(_tls_check(Settings(), args.host, args.port)))
    if args.command == "closure":
        return _closure(Settings(), args)
    if args.command == "config":
        text, code = asyncio.run(_config(Settings(), args))
        sys.stdout.write(text)
        return code
    if args.command == "settings" and args.action in ("set", "reset"):
        text, code = asyncio.run(_change_setting(Settings(), args))
        sys.stdout.write(text)
        return code
    if args.command == "settings":
        text, code = _settings(Settings(), args.action, getattr(args, "key", None))
        sys.stdout.write(text)
        return code
    if args.command == "settings-doc":
        sys.stdout.write(render_settings_doc())
        return 0
    if args.command == "update-claim":
        sys.stdout.write(asyncio.run(_update_claim(Settings())))
        return 0
    if args.command == "update-progress":
        text, code = asyncio.run(_update_progress(Settings(), args.request_id, args.step))
        sys.stdout.write(text)
        return code
    if args.command == "update-report":
        text, code = asyncio.run(
            _update_report(Settings(), args.request_id, args.status, args.reason)
        )
        sys.stdout.write(text)
        return code
    if args.command == "backup-db":
        sys.stdout.write(_backup(Settings(), args.name, prune=args.prune))
        return 0
    if args.command == "restore-db":
        sys.stdout.write(_restore(Settings(), args.name))
        return 0
    if args.command == "maintenance":
        sys.stdout.write(asyncio.run(_maintenance(Settings(), args.action, args.reason, args.by)))
        return 0
    if args.command == "webhooks-list":
        return _write(asyncio.run(_webhooks_list(Settings())))
    if args.command == "webhooks-reset":
        return _write(
            asyncio.run(_webhooks_reset(Settings(), confirmed=args.yes, reregister=args.reregister))
        )
    if args.command == "webhooks-health":
        return _write(asyncio.run(_webhooks_health(Settings())))
    if args.command == "seed-closures":
        return _write(asyncio.run(_seed_closures(Settings())))
    if args.command == "authz-matrix":
        sys.stdout.write(render_markdown(Translator(load_catalogues())))
        return 0
    if args.command == "admin-map":
        sys.stdout.write(navigation.render_markdown(Translator(load_catalogues())))
        return 0
    if args.command == "wxcc-check":
        report, passed = asyncio.run(_wxcc_check(Settings()))
        sys.stdout.write(report)
        return 0 if passed else 1
    if args.command == "wxcc-config-check":
        report, passed_all = asyncio.run(_wxcc_config_check(Settings()))
        sys.stdout.write(report)
        return 0 if passed_all else 1
    if args.command == "register-webhooks":
        report = asyncio.run(_register_webhooks(Settings()))
        sys.stdout.write(report)
        return 1 if ": refused" in report or "no webhooks registered" in report else 0
    sys.stdout.write(asyncio.run(_seed_demo(Settings())))
    return 0


def _write(outcome: tuple[str, bool]) -> int:
    """Print a report; return exit code 0 when it passed, else 1."""
    report, ok = outcome
    sys.stdout.write(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
