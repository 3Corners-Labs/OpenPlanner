"""Command-line entry point for operators and demos.

Usage:
    ``python -m app.cli seed-demo``     create the demo organisations and groups
    ``python -m app.cli authz-matrix``  print the authorisation matrix (docs/authorization.md)
    ``python -m app.cli register-webhooks``  register Webex webhooks and the WxCC subscription
    ``python -m app.cli wxcc-check``  probe every Webex/WxCC call; status and missing scope
    ``python -m app.cli wxcc-config-check``  compare WxCC's routing configuration per group
    ``python -m app.cli webhooks-list``  list every registration: current, stale or foreign
    ``python -m app.cli webhooks-reset [--yes] [--reregister]``  delete stale registrations
    ``python -m app.cli webhooks-health``  token expiry and a signed delivery self-test

Run them inside the ``web`` container (``docker compose exec web ...``): they share its
database (one token store) and its secrets. The webhook commands store their last result for
the admin portal's integrations page.
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine
from app.adapters.db.reports import HEALTH_REPORT, REGISTRATIONS_REPORT, ReportStore
from app.adapters.db.seed import DEMO_SLUG, seed_demo
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
from app.adapters.webex_auth import AuthorizedClient, TokenHealth, WebexTokens
from app.adapters.webhook_registration import register_webex, register_wxcc
from app.adapters.webhook_selftest import deliveries_to_report, delivery_checks, self_test
from app.adapters.wxcc_config import GroupRouting, check_groups, fetch_config, region_check
from app.config import Settings
from app.domain.authorization import Scope
from app.domain.ports import IntegrationError
from app.domain.scheduling import Group
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
    commands.add_parser("authz-matrix", help="print the authorisation matrix as Markdown")
    commands.add_parser("register-webhooks", help="register Webex webhooks and WxCC subscription")
    commands.add_parser("wxcc-check", help="probe the Webex and WxCC calls (status, scope hints)")
    commands.add_parser("wxcc-config-check", help="check WxCC's routing configuration per group")
    commands.add_parser("webhooks-list", help="list registrations: current, stale or foreign")
    reset = commands.add_parser("webhooks-reset", help="delete stale registrations (dry run)")
    reset.add_argument("--yes", action="store_true", help="really delete them")
    reset.add_argument(
        "--reregister", action="store_true", help="with --yes: run register-webhooks afterwards"
    )
    commands.add_parser("webhooks-health", help="token expiry and a signed delivery self-test")
    args = parser.parse_args(argv)
    if args.command == "webhooks-list":
        return _write(asyncio.run(_webhooks_list(Settings())))
    if args.command == "webhooks-reset":
        return _write(
            asyncio.run(_webhooks_reset(Settings(), confirmed=args.yes, reregister=args.reregister))
        )
    if args.command == "webhooks-health":
        return _write(asyncio.run(_webhooks_health(Settings())))
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
