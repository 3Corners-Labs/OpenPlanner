"""``config export --format json``, ``config plan`` and ``config apply`` (ADR 0099 point 13).

The host commands of the configuration document. They run as the host (``system:cli``), which
the matrix does not bind, with the passphrase from a file and never from the command line.
A plan is printed as lines of action, kind and natural key with the names of the fields it
touches, never a value; ``apply`` prints the plan hash it applied.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from app.adapters.clock import SystemClock
from app.adapters.configuration_context import document_service
from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.discovery import GuardedDiscoveryFetcher
from app.adapters.integrations import secret_box
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.configuration_document import Scope
from app.domain.configuration_document_service import (
    Actor,
    ApplyResult,
    ConfigurationDocumentService,
)
from app.domain.configuration_json_schema import json_schema
from app.domain.configuration_plan import Plan
from app.domain.configuration_v1 import V1
from app.domain.errors import DomainError, ErrorCode

ACTOR = Actor.host()


def add_arguments(
    export_cmd: argparse.ArgumentParser,
    plan_cmd: argparse.ArgumentParser,
    apply_cmd: argparse.ArgumentParser,
) -> None:
    """Add the options of the document commands to the parsers of ``config``."""
    export_cmd.add_argument(
        "--format", choices=("sqlite", "json"), default="sqlite", help="the file format"
    )
    export_cmd.add_argument(
        "--no-secrets", action="store_true", help="json only: leave every secret out"
    )
    for sub in (export_cmd, plan_cmd, apply_cmd):
        sub.add_argument("--organisation", metavar="SLUG", help="json only: one organisation")
    for sub in (plan_cmd, apply_cmd):
        sub.add_argument("path", type=Path, help="the configuration document")
        sub.add_argument(
            "--passphrase-file", type=Path, metavar="PATH", help="opens the document's secrets"
        )
    apply_cmd.add_argument("--plan-hash", metavar="HASH", help="the hash `plan` printed")


def passphrase_from(path: Path | None) -> str | None:
    """Return the first line of ``path``, or ``None`` when no file is given."""
    if path is None:
        return None
    with path.open(encoding="utf-8") as handle:
        return handle.readline().rstrip("\r\n")


def _service(
    settings: Settings, http: httpx.AsyncClient
) -> tuple[ConfigurationDocumentService, object]:
    """Build the service on the installation's database; return it with the engine.

    ``http`` is the client the guarded discovery fetch of a new or moved provider uses.
    """
    engine = create_engine(settings.database_url)
    clock = SystemClock()
    uow = SqlUnitOfWork(engine, secret_box(settings))
    service = document_service(
        uow,
        clock,
        AdminService(uow, clock),
        settings=lambda: settings,
        discovery=GuardedDiscoveryFetcher(http, allow_private=settings.environment != "prod"),
    )
    return service, engine


def _report(plan: Plan) -> str:
    """Return a plan as lines, then its refusals and its hash."""
    lines = [
        *plan.lines(),
        *(f"REFUSED {r.path} {r.code.value} {r.field or ''}".rstrip() for r in plan.refusals),
    ]
    lines.append(f"{len(plan.changes)} change(s), {len(plan.refusals)} refusal(s)")
    lines.append(f"plan hash {plan.digest}")
    return "\n".join(lines) + "\n"


async def run(settings: Settings, args: argparse.Namespace) -> tuple[str, int]:
    """Run ``config export --format json``, ``config plan`` or ``config apply``."""
    scope = Scope(args.organisation) if args.organisation else Scope()
    async with httpx.AsyncClient(timeout=10.0) as http:
        return await _run(settings, args, scope, *_service(settings, http))


async def _run(
    settings: Settings,
    args: argparse.Namespace,
    scope: Scope,
    service: ConfigurationDocumentService,
    engine: object,
) -> tuple[str, int]:
    """Run the command on a built service; dispose the engine at the end."""
    try:
        passphrase = passphrase_from(args.passphrase_file)
        if args.action == "export":
            data = await service.export(ACTOR, scope, passphrase, secrets=not args.no_secrets)
            write_private(args.path, data)
            return f"document written: {args.path}\n", 0
        document = await asyncio.to_thread(_read_limited, Path(args.path), settings)
        if args.action == "plan":
            plan = await service.plan(ACTOR, document, passphrase, scope=_pinned(args))
            return _report(plan), 0 if plan.ok else 2
        result: ApplyResult = await service.apply(
            ACTOR, document, passphrase, args.plan_hash, scope=_pinned(args)
        )
        text = _report(result.plan)
        if not result.applied:
            return text + "nothing applied\n", 2
        return text + f"applied; settings version {result.version}\n", 0
    except DomainError as error:
        return f"Refused ({error.code.value}).\n", 2
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


def _read_limited(path: Path, settings: Settings) -> bytes:
    """Read a document, refusing a file above the size limit before reading it."""
    limit = settings.configuration_package_max_mb * 1024 * 1024
    if path.stat().st_size > limit:
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE, "document")
    return path.read_bytes()


def _pinned(args: argparse.Namespace) -> Scope | None:
    """Return the scope the command pins the document to, if it names an organisation."""
    return Scope(args.organisation) if args.organisation else None


def write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` with mode 600, replacing an existing file atomically.

    The data goes to a new file next to the target and is renamed over it, so a failed write
    never loses the old file or leaves half a document under the target's name.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def render_schema() -> str:
    """Return the JSON Schema of the newest format as ``docs/configuration-schema`` holds it."""
    return json.dumps(json_schema(V1), indent=2, ensure_ascii=False) + "\n"
