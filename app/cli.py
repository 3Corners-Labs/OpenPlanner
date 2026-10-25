"""Command-line entry point for operators and demos.

Usage:
    ``python -m app.cli seed-demo``     create the demo organisations and groups
    ``python -m app.cli authz-matrix``  print the authorisation matrix (docs/authorization.md)
    ``python -m app.cli register-webhooks``  register Webex webhooks and the WxCC subscription
    ``python -m app.cli wxcc-check``  probe every Webex/WxCC call; status and missing scope
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine
from app.adapters.db.seed import DEMO_SLUG, seed_demo
from app.adapters.diagnostics import format_report, passed, probes, run_checks
from app.adapters.integrations import build_integrations
from app.adapters.webex_auth import AuthorizedClient, WebexTokens
from app.adapters.webhook_registration import register_webex, register_wxcc
from app.config import Settings
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
    report = format_report(results)
    if not settings.wxcc_api_base_url:
        report += "WXCC_API_BASE_URL is not set: WxCC was not checked.\n"
    return report, passed(results)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="create the demo organisations and groups")
    commands.add_parser("authz-matrix", help="print the authorisation matrix as Markdown")
    commands.add_parser("register-webhooks", help="register Webex webhooks and WxCC subscription")
    commands.add_parser("wxcc-check", help="probe the Webex and WxCC calls (status, scope hints)")
    args = parser.parse_args(argv)
    if args.command == "authz-matrix":
        sys.stdout.write(render_markdown(Translator(load_catalogues())))
        return 0
    if args.command == "wxcc-check":
        report, passed = asyncio.run(_wxcc_check(Settings()))
        sys.stdout.write(report)
        return 0 if passed else 1
    if args.command == "register-webhooks":
        report = asyncio.run(_register_webhooks(Settings()))
        sys.stdout.write(report)
        return 1 if ": refused" in report else 0
    sys.stdout.write(asyncio.run(_seed_demo(Settings())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
