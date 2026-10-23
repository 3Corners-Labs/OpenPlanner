"""Command-line entry point for operators and demos.

Usage:
    ``python -m app.cli seed-demo``     create the demo organisations and groups
    ``python -m app.cli authz-matrix``  print the authorisation matrix (docs/authorization.md)
    ``python -m app.cli register-webhooks``  register Webex webhooks and the WxCC subscription
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import httpx

from app.adapters.clock import SystemClock
from app.adapters.db.engine import create_engine
from app.adapters.db.seed import DEMO_SLUG, seed_demo
from app.adapters.integrations import build_integrations
from app.adapters.webex_auth import AuthorizedClient
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


async def _register_webhooks(settings: Settings) -> str:
    """Register the webhooks the configured integrations need; return a report."""
    base = settings.public_base_url.rstrip("/")
    if not settings.video_configured or settings.webex_webhook_secret is None:
        return (
            "Webex is not configured (WEBEX_CLIENT_ID and WEBEX_WEBHOOK_SECRET); nothing to do.\n"
        )
    if not base.startswith("https://"):
        return "PUBLIC_BASE_URL must be https: Webex and WxCC only call public https URLs.\n"
    engine = create_engine(settings.database_url)
    async with httpx.AsyncClient(timeout=15.0) as http:
        integrations = build_integrations(settings, engine, http, SystemClock())
        assert integrations.tokens is not None  # noqa: S101 - implied by video_configured
        client = AuthorizedClient(http, integrations.tokens)
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
    await engine.dispose()
    return "".join(f"{line}\n" for line in lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="create the demo organisations and groups")
    commands.add_parser("authz-matrix", help="print the authorisation matrix as Markdown")
    commands.add_parser("register-webhooks", help="register Webex webhooks and WxCC subscription")
    args = parser.parse_args(argv)
    if args.command == "authz-matrix":
        sys.stdout.write(render_markdown(Translator(load_catalogues())))
        return 0
    if args.command == "register-webhooks":
        sys.stdout.write(asyncio.run(_register_webhooks(Settings())))
        return 0
    sys.stdout.write(asyncio.run(_seed_demo(Settings())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
