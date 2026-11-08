"""Fake host tools for the updater and the installer: docker, cosign, curl, sudo, systemctl...

The test puts one small wrapper per tool on ``PATH``; each runs this file with the tool name as
its first argument. Every call is appended to ``calls.jsonl`` in ``FAKE_HOST_DIR`` together with
the image pinned at that moment, so a test can assert what the updater did and in which order.

Behaviour is set through files in ``FAKE_HOST_DIR``:

- ``claim``: what ``app.cli update-claim`` prints.
- ``revision``: what ``alembic current`` prints as the revision.
- ``fail``: one operation per line that exits 1 (``pull``, ``cosign``, ``image-ls``, ``rmi``,
  ``backup-db``, ``restore-db``, ``sessions``, ``downgrade``, ``docker-info``, ``update-progress``,
  ``maintenance``;
  ``pull-proxy`` and ``cosign-proxy`` fail only for the proxy image, ADR 0064).
- ``compose-version``: what ``docker compose version --short`` prints (default 2.40.3).
- ``engine-version``: what ``docker version --format {{.Server.Version}}`` prints (default 29.1.3).
- ``edge-ipv6``: whether the project's ``edge`` network has IPv6, as ``docker network inspect``
  says (default ``true``).
- ``latest``: the tag of the latest GitHub release (default none: the API call fails).
- ``min-upgrade-from``: the ``MIN_UPGRADE_FROM`` every image's configuration carries (ADR
  0116; default none); ``fail`` may list ``upgrade-path-check`` (a database too old).
- ``images``: the images on the host, one ``repository:tag`` per line, or ``repository@digest``
  for one without a tag; ``docker image ls`` lists them, ``docker rmi`` only records the call.
- ``unhealthy``: one image reference per line; the worker (or, for a proxy image, the proxy)
  reports ``unhealthy`` while it is pinned.
- ``cosign-version``: the installed cosign (default v3.1.4, the latest the fake GitHub offers).
- ``installed``: Debian packages ``dpkg -s`` finds; ``upgradable``: ``apt list --upgradable``.
- ``docker-key``: the fingerprint of Docker's release key (default the real one).
- ``config-env``: what ``app.cli config env --out -`` prints (default: a login of four
  variables); ``fail`` may list ``config-env`` (a wrong passphrase), ``config-import`` (an
  object in use), ``config-plan`` (a refusal in the plan, ADR 0099) and ``config-apply`` (a
  stale plan), ``config-passphrase`` (a wrong passphrase for a document); they exit 2 with the
  CLI's ``Refused (...)`` or ``REFUSED ...`` lines.
  ``config plan`` prints one change and the plan hash ``PLAN_HASH``; ``config apply`` refuses
  any other ``--plan-hash`` with ``configuration_plan_stale``.

``apt-get``, ``usermod`` and ``systemctl`` only record their call; ``curl -o`` writes a small
stand-in for the downloaded file, with a matching checksum unless ``fail`` lists ``checksum``.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

WORKER = "worker-container-id"
WEB = "web-container-id"
PROJECT = "openplanner"
CONFIG_ENV = (
    "OIDC_DISCOVERY_URL=https://idp.example.nl/.well-known/openid-configuration\n"
    "OIDC_CLIENT_ID=openplanner-admin\n"
    "OIDC_CLIENT_SECRET=secret-from-the-package\n"
    "OIDC_SCOPES=openid profile email\n"
)
CONFIG_SUMMARY = "configuration imported\norganisations       2\ngroups              3\n"
PLAN_HASH = "ab" * 32
PROXY = "proxy-container-id"
COSIGN = "3.1.4"
# The releases the fake registry knows, so an image's digest can be mapped back to the version
# baked into it (``APP_VERSION``, read by the installer; ADR 0064 step 2).
RELEASES = ("1.3.0", "1.4.0", "1.5.0", "1.5.0-rc.3")
COSIGN_DEB = b"cosign package"
DOCKER_KEY = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"


def digest(image: str, version: str) -> str:
    """Return the digest reference the fake registry gives ``image:version``."""
    return f"{image}@sha256:{hashlib.sha256(version.encode()).hexdigest()}"


def _host() -> Path:
    return Path(os.environ["FAKE_HOST_DIR"])


def _lines(name: str) -> list[str]:
    path = _host() / name
    return path.read_text().splitlines() if path.exists() else []


def _pin(key: str) -> str:
    pin_file = Path(os.environ["FAKE_PIN_FILE"])
    if not pin_file.exists():
        return ""
    for line in pin_file.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.removeprefix(f"{key}=")
    return ""


def _pinned() -> str:
    return _pin("OPENPLANNER_IMAGE")


def _pinned_proxy() -> str:
    return _pin("OPENPLANNER_PROXY_IMAGE")


def _is_proxy(reference: str) -> bool:
    return "-proxy:" in reference or "-proxy@" in reference


def _version_of(reference: str) -> str:
    """Return the release whose digest ``reference`` is, as the image's ``APP_VERSION``."""
    image = reference.partition("@")[0]
    known = (*RELEASES, *(line.removeprefix("v") for line in _lines("latest")))
    return next((version for version in known if digest(image, version) == reference), "")


def _failing(operation: str) -> int:
    return 1 if operation in _lines("fail") else 0


def _config(args: list[str]) -> int:
    """Handle ``app.cli config env|import`` as the host's one-off container would."""
    if args[0] == "env":
        if _failing("config-env"):
            print("Refused (configuration_passphrase_wrong).")
            return 2
        path = _host() / "config-env"
        sys.stdout.write(path.read_text() if path.exists() else CONFIG_ENV)
    elif args[0] == "import":
        if _failing("config-import"):
            print("Refused (configuration_object_in_use).")
            return 2
        sys.stdout.write(CONFIG_SUMMARY)
    elif args[0] in ("plan", "apply"):
        return _document(args)
    return 0


def _document(args: list[str]) -> int:
    """Handle ``app.cli config plan|apply FILE`` of ADR 0099 the way the CLI reports them."""
    if _failing("config-passphrase"):
        print("Refused (configuration_passphrase_wrong).")
        return 2
    if args[0] == "plan":
        if _failing("config-plan"):
            print("REFUSED organisations[acme].groups[team] not_permitted name")
            print(f"0 change(s), 1 refusal(s)\nplan hash {PLAN_HASH}")
            return 2
        print(f"create group acme/team: name\n1 change(s), 0 refusal(s)\nplan hash {PLAN_HASH}")
        return 0
    given = args[args.index("--plan-hash") + 1] if "--plan-hash" in args else ""
    if _failing("config-apply") or given != PLAN_HASH:
        print("Refused (configuration_plan_stale).")
        return 2
    print(f"create group acme/team: name\n1 change(s), 0 refusal(s)\nplan hash {PLAN_HASH}")
    print("applied; settings version 4")
    return 0


def _compose(args: list[str]) -> int:
    """Handle ``docker compose --project-directory D --env-file F <command>``."""
    command = args[5:]
    if command[:3] == ["exec", "-T", "web"]:
        command = command[3:]
    elif command[:1] == ["run"] and "web" in command:
        command = command[command.index("web") + 1 :]  # the options before the service
    if command == ["ps", "-q", "worker"]:
        print(WORKER)
    elif command == ["ps", "-a", "-q", "web"]:
        print(WEB)
    elif command == ["ps", "-q", "proxy"]:
        print(PROXY)
    elif command[:2] == ["alembic", "current"]:
        print(f"{(_host() / 'revision').read_text().strip()} (head)")
    elif command[:2] == ["alembic", "downgrade"]:
        return _failing("downgrade")
    elif command[:3] == ["python", "-m", "app.cli"]:
        if command[3] == "update-claim":
            sys.stdout.write((_host() / "claim").read_text())
        elif command[3] == "backup-db" and command[4:] != ["--prune"]:
            return _failing("backup-db")
        elif command[3] == "upgrade-path-check":
            return _failing("upgrade-path-check")
        elif command[3] in ("restore-db", "update-progress", "maintenance", "sessions"):
            return _failing(command[3])
        elif command[3] == "config":
            return _config(command[4:])
    return 0


def _images(repository: str) -> None:
    """Print ``docker image ls`` for ``repository``: digest reference, then tag reference."""
    for line in _lines("images"):
        if "@" in line:
            name, _, _ = line.partition("@")
            if name == repository:
                print(f"{line} {name}:<none>")
            continue
        name, _, tag = line.rpartition(":")
        if name == repository:
            print(f"{digest(name, tag)} {line}")


def _docker(args: list[str]) -> int:
    if args[:2] == ["compose", "version"]:
        print(next(iter(_lines("compose-version")), "2.40.3"))
    elif args[0] == "compose":
        return _compose(args)
    elif args[0] == "info":
        return _failing("docker-info")
    elif args[0] == "version":
        print(next(iter(_lines("engine-version")), "29.1.3"))
    elif args[:2] == ["network", "inspect"] and args[-1] == f"{PROJECT}_edge":
        print(next(iter(_lines("edge-ipv6")), "true"))
    elif args[0] == "inspect" and any("com.docker.compose.project" in arg for arg in args):
        print(PROJECT)
        return 0
    if args[0] == "pull":
        return _failing("pull") or (_is_proxy(args[-1]) and _failing("pull-proxy"))
    if args[0] == "rmi":
        return _failing("rmi")
    if args[:2] == ["image", "ls"]:
        if _failing("image-ls"):
            return 1
        _images(args[-1])
    if args[:2] == ["image", "inspect"] and any(".Config.Env" in arg for arg in args):
        print(f"APP_VERSION={_version_of(args[-1])}")
        for line in _lines("min-upgrade-from"):
            print(f"MIN_UPGRADE_FROM={line}")
    elif args[:2] == ["image", "inspect"]:
        image, _, version = args[-1].rpartition(":")
        print(digest(image, version))
    elif args[0] == "inspect":
        pinned = _pinned_proxy() if args[-1] == PROXY else _pinned()
        print("unhealthy" if pinned in _lines("unhealthy") else "healthy")
    return 0


def _download(args: list[str]) -> int:
    """Handle ``curl -o FILE URL``: write a stand-in for the release file."""
    target, url = Path(args[args.index("-o") + 1]), args[-1]
    if url.endswith(".deb"):
        target.write_bytes(COSIGN_DEB)
    elif url.endswith("cosign_checksums.txt"):
        version = url.split("/")[-2].removeprefix("v")
        checksum = "0" * 64 if _failing("checksum") else hashlib.sha256(COSIGN_DEB).hexdigest()
        target.write_text(f"{checksum}  cosign_{version}_amd64.deb\n")
    else:
        target.write_text("release key\n")
    return 0


def _curl(args: list[str]) -> int:
    if "-o" in args:
        return _download(args)
    if any("api.github.com/repos/sigstore" in arg for arg in args):
        print(json.dumps({"tag_name": f"v{COSIGN}"}))
    elif any("api.github.com" in arg for arg in args):
        latest = _lines("latest")
        if not latest:
            return 22
        release = {"tag_name": latest[0], "name": latest[0]}
        if any("/releases?" in arg for arg in args):  # the rc channel lists releases
            release["prerelease"] = "-rc." in latest[0]
            print(json.dumps([release]))
        else:
            print(json.dumps(release))
    return 0


def _packages(tool: str, args: list[str]) -> int:
    """Answer dpkg, apt and gpg the way an Ubuntu host would."""
    if tool == "dpkg" and args == ["--print-architecture"]:
        print("amd64")
    elif tool == "dpkg":
        return 0 if args[-1] in _lines("installed") else 1
    elif tool == "apt":
        print("\n".join(f"{name}/noble-updates 2.0 amd64" for name in _lines("upgradable")))
    elif tool == "gpg":
        print(f"fpr:::::::::{next(iter(_lines('docker-key')), DOCKER_KEY)}:")
    return 0


def main() -> int:
    """Record the call, then act as the tool named in ``sys.argv[1]``."""
    tool, args = sys.argv[1], sys.argv[2:]
    with (_host() / "calls.jsonl").open("a") as calls:
        call = {"tool": tool, "args": args, "pinned": _pinned(), "proxy": _pinned_proxy()}
        calls.write(json.dumps(call) + "\n")
    if tool == "docker":
        return _docker(args)
    if tool == "cosign" and args == ["version"]:
        print(f"GitVersion:    {next(iter(_lines('cosign-version')), 'v' + COSIGN)}")
    elif tool == "cosign":
        return _failing("cosign") or (any(map(_is_proxy, args)) and _failing("cosign-proxy"))
    if tool == "curl":
        return _curl(args)
    if tool in ("dpkg", "apt", "gpg"):
        return _packages(tool, args)
    if tool == "sha256sum":
        print(f"{hashlib.sha256(Path(args[0]).read_bytes()).hexdigest()}  {args[0]}")
    if tool == "id":
        print("operator docker" if args[:1] == ["-nG"] else "operator")
    if tool == "sleep":
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
