"""Command-line entry point for operators and demos.

Usage: ``python -m app.cli seed-demo`` (after ``alembic upgrade head``).
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import DEMO_SLUG, seed_demo
from app.config import Settings


async def _seed_demo(settings: Settings) -> str:
    """Seed the demo group and return a one-line report."""
    engine = create_engine(settings.database_url)
    try:
        created = await seed_demo(engine)
    finally:
        await engine.dispose()
    state = "created" if created else "already present"
    return f"Demo group {state}: {settings.public_base_url.rstrip('/')}/g/{DEMO_SLUG}\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="create the demo organisation and group")
    parser.parse_args(argv)
    sys.stdout.write(asyncio.run(_seed_demo(Settings())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
