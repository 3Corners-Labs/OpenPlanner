"""Fake host tools for the updater script (ADR 0029): docker, cosign, curl, flock and sleep.

The test puts one small wrapper per tool on ``PATH``; each runs this file with the tool name as
its first argument. Every call is appended to ``calls.jsonl`` in ``FAKE_HOST_DIR`` together with
the image pinned at that moment, so a test can assert what the updater did and in which order.

Behaviour is set through files in ``FAKE_HOST_DIR``:

- ``claim``: what ``app.cli update-claim`` prints.
- ``revision``: what ``alembic current`` prints as the revision.
- ``fail``: one operation per line that exits 1 (``pull``, ``cosign``, ``backup-db``,
  ``restore-db``, ``downgrade``).
- ``unhealthy``: one image reference per line; the worker reports ``unhealthy`` while it is
  pinned.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

WORKER = "worker-container-id"


def digest(image: str, version: str) -> str:
    """Return the digest reference the fake registry gives ``image:version``."""
    return f"{image}@sha256:{hashlib.sha256(version.encode()).hexdigest()}"


def _host() -> Path:
    return Path(os.environ["FAKE_HOST_DIR"])


def _lines(name: str) -> list[str]:
    path = _host() / name
    return path.read_text().splitlines() if path.exists() else []


def _pinned() -> str:
    pin_file = Path(os.environ["FAKE_PIN_FILE"])
    if not pin_file.exists():
        return ""
    for line in pin_file.read_text().splitlines():
        if line.startswith("OPENPLANNER_IMAGE="):
            return line.removeprefix("OPENPLANNER_IMAGE=")
    return ""


def _failing(operation: str) -> int:
    return 1 if operation in _lines("fail") else 0


def _compose(args: list[str]) -> int:
    """Handle ``docker compose --project-directory D --env-file F <command>``."""
    command = args[5:]
    if command[:3] == ["exec", "-T", "web"]:
        command = command[3:]
    elif command[:5] == ["run", "--rm", "--no-deps", "-T", "web"]:
        command = command[5:]
    if command == ["ps", "-q", "worker"]:
        print(WORKER)
    elif command[:2] == ["alembic", "current"]:
        print(f"{(_host() / 'revision').read_text().strip()} (head)")
    elif command[:2] == ["alembic", "downgrade"]:
        return _failing("downgrade")
    elif command[:3] == ["python", "-m", "app.cli"]:
        if command[3] == "update-claim":
            sys.stdout.write((_host() / "claim").read_text())
        elif command[3] == "backup-db" and command[4:] != ["--prune"]:
            return _failing("backup-db")
        elif command[3] == "restore-db":
            return _failing("restore-db")
    return 0


def _docker(args: list[str]) -> int:
    if args[0] == "compose":
        return _compose(args)
    if args[0] == "pull":
        return _failing("pull")
    if args[:2] == ["image", "inspect"]:
        image, _, version = args[-1].rpartition(":")
        print(digest(image, version))
    elif args[0] == "inspect":
        print("unhealthy" if _pinned() in _lines("unhealthy") else "healthy")
    return 0


def main() -> int:
    """Record the call, then act as the tool named in ``sys.argv[1]``."""
    tool, args = sys.argv[1], sys.argv[2:]
    with (_host() / "calls.jsonl").open("a") as calls:
        calls.write(json.dumps({"tool": tool, "args": args, "pinned": _pinned()}) + "\n")
    if tool == "docker":
        return _docker(args)
    if tool == "cosign":
        return _failing("cosign")
    if tool == "sleep":
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
