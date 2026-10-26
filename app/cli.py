"""Command-line entry point for operators and demos.

Usage:
    ``python -m app.cli seed-demo``     create the demo organisations and groups
    ``python -m app.cli authz-matrix``  print the authorisation matrix (docs/authorization.md)
    ``python -m app.cli register-webhooks``  register Webex webhooks and the WxCC subscription
    ``python -m app.cli wxcc-check``  probe every Webex/WxCC call; status and missing scope
    ``python -m app.cli wxcc-config-check``  compare WxCC's routing configuration per group

Host updater (ADR 0028; run inside the ``web`` container by ``scripts/updater``):
    ``python -m app.cli update-claim``  claim the waiting request: ``id action target from``
    ``python -m app.cli update-report ID STATUS [REASON]``  record the outcome
    ``python -m app.cli backup-db [NAME] [--prune]``  back up the database, delete old backups
    ``python -m app.cli restore-db NAME``  put a backup back (application stopped)
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from app.adapters.db.seed import DEMO_SLUG, seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.diagnostics import CheckResult, format_report, passed, probes, run_checks
from app.adapters.integrations import build_integrations
from app.adapters.releases import update_service
from app.adapters.webex_auth import AuthorizedClient, WebexTokens
from app.adapters.webhook_registration import register_webex, register_wxcc
from app.adapters.wxcc_config import GroupRouting, check_groups, fetch_config, region_check
from app.config import Settings
from app.domain.authorization import Scope
from app.domain.errors import DomainError
from app.domain.ports import IntegrationError
from app.domain.scheduling import Group
from app.domain.update_service import UpdateService
from app.domain.updates import OUTCOMES
from app.i18n import Translator, load_catalogues
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


async def _register_webhooks(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Register the webhooks the configured integrations need; return a report."""
    base = settings.webhook_base
    if not settings.video_configured or settings.webex_webhook_secret is None:
        return (
            "Webex is not configured (WEBEX_CLIENT_ID and WEBEX_WEBHOOK_SECRET); nothing to do.\n"
        )
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
            hosts=[g.video_host_email for g in await _groups(settings) if g.video_host_email],
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


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="create the demo organisations and groups")
    commands.add_parser("authz-matrix", help="print the authorisation matrix as Markdown")
    commands.add_parser("register-webhooks", help="register Webex webhooks and WxCC subscription")
    commands.add_parser("wxcc-check", help="probe the Webex and WxCC calls (status, scope hints)")
    commands.add_parser("wxcc-config-check", help="check WxCC's routing configuration per group")
    commands.add_parser("update-claim", help="claim the waiting update request (host updater)")
    report_cmd = commands.add_parser("update-report", help="record an update's outcome")
    report_cmd.add_argument("request_id", type=int)
    report_cmd.add_argument("status", choices=sorted(s.value for s in OUTCOMES))
    report_cmd.add_argument("reason", nargs="?", default="")
    backup_cmd = commands.add_parser("backup-db", help="back up the database (data/backups)")
    backup_cmd.add_argument("name", nargs="?")
    backup_cmd.add_argument("--prune", action="store_true", help="delete backups older than 24 h")
    restore_cmd = commands.add_parser("restore-db", help="put a backup back (app stopped)")
    restore_cmd.add_argument("name")
    args = parser.parse_args(argv)
    if args.command == "update-claim":
        sys.stdout.write(asyncio.run(_update_claim(Settings())))
        return 0
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


if __name__ == "__main__":
    sys.exit(main())
