"""Command-line entry point for operators and demos.

Usage:
    ``python -m app.cli seed-demo``     create the demo organisations and groups
    ``python -m app.cli seed-closures``  add the closure examples (e2e pages); not in production
    ``python -m app.cli authz-matrix``  print the authorisation matrix (docs/authorization.md)
    ``python -m app.cli register-webhooks``  register Webex webhooks and the WxCC subscription
    ``python -m app.cli wxcc-check``  probe every Webex/WxCC call; status and missing scope
    ``python -m app.cli wxcc-config-check``  compare WxCC's routing configuration per group
    ``python -m app.cli client-pseudonym ADDRESS``  the pseudonym security events use for an
        IP address the security team already has (ADR 0053); there is no way back

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

Run them inside the ``web`` container (``docker compose exec web ...``): they share its
database (one token store) and its secrets. The webhook commands store their last result for
the admin portal's integrations page.
"""

import argparse
import asyncio
import dataclasses
import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.adapters.clock import SystemClock
from app.adapters.db.backup import (
    backup_database,
    database_path,
    prune_backups,
    restore_database,
)
from app.adapters.db.engine import create_engine
from app.adapters.db.reports import HEALTH_REPORT, REGISTRATIONS_REPORT, ReportStore
from app.adapters.db.seed import DEMO_SLUG, seed_closure_examples, seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.diagnostics import CheckResult, format_report, passed, probes, run_checks
from app.adapters.integrations import build_integrations
from app.adapters.registrations import (
    Expected,
    Listing,
    delete_stale,
    format_listing,
    list_registrations,
    listing_to_report,
)
from app.adapters.releases import update_service
from app.adapters.webex_auth import AuthorizedClient, TokenHealth, WebexTokens
from app.adapters.webhook_registration import register_webex, register_wxcc
from app.adapters.webhook_selftest import deliveries_to_report, delivery_checks, self_test
from app.adapters.wxcc_config import GroupRouting, check_groups, fetch_config, region_check
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.authorization import Scope
from app.domain.client_pseudonym import UNKNOWN_CLIENT, ClientPseudonyms
from app.domain.errors import DomainError
from app.domain.maintenance import MaintenanceReason, MaintenanceStatus
from app.domain.maintenance_service import MaintenanceService
from app.domain.ports import IntegrationError
from app.domain.scheduling import Group
from app.domain.update_service import UpdateService
from app.domain.updates import OUTCOMES, REPORTED_STEPS
from app.i18n import Translator, load_catalogues
from app.keys import client_pseudonym_key, management_token_key
from app.web.authz_report import render_markdown


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

    Refused in production: the examples include a booking of a made-up citizen.
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
        checks = probes(settings.webex_api_base_url, settings.wxcc_api_base_url)
        results = await run_checks(http, tokens, checks, SystemClock())
        if settings.wxcc_api_base_url and results[0].ok:
            token = await tokens.access_token()
            results.append((await region_check(http, token, settings.wxcc_api_base_url))[0])
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


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="create the demo organisations and groups")
    commands.add_parser("seed-closures", help="add the closure examples (not in production)")
    commands.add_parser("authz-matrix", help="print the authorisation matrix as Markdown")
    commands.add_parser("register-webhooks", help="register Webex webhooks and WxCC subscription")
    commands.add_parser("wxcc-check", help="probe the Webex and WxCC calls (status, scope hints)")
    commands.add_parser("wxcc-config-check", help="check WxCC's routing configuration per group")
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
    args = parser.parse_args(argv)
    if args.command == "client-pseudonym":
        pseudonym = ClientPseudonyms(client_pseudonym_key(Settings())).of(args.address)
        sys.stdout.write(f"{pseudonym}\n")
        return 0 if pseudonym != UNKNOWN_CLIENT else 1
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
