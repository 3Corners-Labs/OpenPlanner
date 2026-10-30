"""The proxy's entrypoint: which certificate source it starts with, and the watcher (ADR 0070).

Runs ``deploy/proxy/entrypoint.sh`` with a fake ``caddy`` on the PATH that logs every ``run``
and ``reload`` with the ``TLS_DIRECTIVE`` it was given, so the shell logic is tested without
Docker. The real proxy is exercised by ``scripts/check-proxy.sh`` in CI.
"""

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).parents[2] / "deploy/proxy/entrypoint.sh"
SH = shutil.which("sh") or "/bin/sh"
FAKE_CADDY = """#!/bin/sh
# Fake caddy: records the command and the TLS_DIRECTIVE it was started or reloaded with.
echo "$1:${TLS_DIRECTIVE-unset}" >> "$CADDY_LOG"
if [ "$1" = run ]; then sleep "${CADDY_RUN_SECONDS:-0}"; fi
"""


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    """A PATH with the fake caddy and the shell tools the script uses."""
    directory = tmp_path / "bin"
    directory.mkdir()
    (directory / "caddy").write_text(FAKE_CADDY)
    (directory / "caddy").chmod(0o755)
    for tool in ("sh", "head", "sleep", "echo"):
        real = shutil.which(tool)
        if real and not (directory / tool).exists():
            os.symlink(real, directory / tool)
    return directory


@dataclass(frozen=True)
class Outcome:
    """How the entrypoint ended: exit code, what it printed, what the fake caddy logged."""

    returncode: int
    stdout: str
    stderr: str
    logged: list[str]


def run(bin_dir: Path, tmp_path: Path, run_seconds: int = 0, **env: str) -> Outcome:
    """Run the entrypoint and return how it ended.

    Output goes to files, not pipes: the watcher the script leaves behind inherits them, and
    with pipes the test would wait for it instead of for Caddy (the real container stops with
    Caddy, PID 1, and so does the watcher, which checks that PID).
    """
    log = tmp_path / "caddy.log"
    portal = tmp_path / "portal-certs"
    portal.mkdir(exist_ok=True)
    environment = {
        "PATH": str(bin_dir),
        "CADDY_LOG": str(log),
        "CADDY_RUN_SECONDS": str(run_seconds),
        "TLS_PORTAL_DIR": str(portal),
        "TLS_PORTAL_POLL_SECONDS": "1",
        **env,
    }
    out, err = tmp_path / "stdout", tmp_path / "stderr"
    with out.open("w") as stdout, err.open("w") as stderr:
        process = subprocess.Popen(  # noqa: S603 - the repository's own script, fake caddy
            [SH, str(ENTRYPOINT)], env=environment, stdout=stdout, stderr=stderr, text=True
        )
        returncode = process.wait(timeout=30)
    time.sleep(1.5)  # let the watcher notice that caddy is gone and write its last line
    lines = log.read_text().splitlines() if log.exists() else []
    return Outcome(returncode, out.read_text(), err.read_text(), lines)


def write_pair(portal: Path, serial: str) -> None:
    """Write the three files the way ``web`` does: the pair first, the serial last."""
    (portal / "fullchain.pem").write_text("chain")
    (portal / "privkey.pem").write_text("key")
    (portal / "serial").write_text(serial + "\n")


def test_without_a_domain_nothing_starts(bin_dir: Path, tmp_path: Path) -> None:
    outcome = run(bin_dir, tmp_path, ACME_EMAIL="ops@example.nl")

    assert outcome.returncode == 1
    assert "TLS_DOMAIN is required" in outcome.stderr
    assert outcome.logged == []


def test_acme_is_the_default_without_any_pair(bin_dir: Path, tmp_path: Path) -> None:
    outcome = run(bin_dir, tmp_path, TLS_DOMAIN="afspraak.example.nl", ACME_EMAIL="ops@example.nl")

    assert outcome.returncode == 0, outcome.stderr
    assert outcome.logged == ["run:"]


def test_a_portal_pair_present_at_start_is_served_from_the_start(
    bin_dir: Path, tmp_path: Path
) -> None:
    portal = tmp_path / "portal-certs"
    portal.mkdir()
    write_pair(portal, "ab" * 32)

    outcome = run(bin_dir, tmp_path, TLS_DOMAIN="afspraak.example.nl", ACME_EMAIL="ops@example.nl")

    assert outcome.returncode == 0, outcome.stderr
    assert outcome.logged == [f"run:tls {portal}/fullchain.pem {portal}/privkey.pem"]
    assert "portal certificate " + "ab" * 32 in outcome.stdout


def test_the_watcher_reloads_when_the_pair_appears_and_when_it_is_withdrawn(
    bin_dir: Path, tmp_path: Path
) -> None:
    portal = tmp_path / "portal-certs"
    portal.mkdir()

    def change_the_pair() -> None:
        time.sleep(0.5)
        write_pair(portal, "cd" * 32)
        time.sleep(2.5)
        (portal / "serial").unlink()

    changer = threading.Thread(target=change_the_pair)
    changer.start()
    outcome = run(
        bin_dir,
        tmp_path,
        run_seconds=6,
        TLS_DOMAIN="afspraak.example.nl",
        ACME_EMAIL="ops@example.nl",
    )
    changer.join()

    assert outcome.returncode == 0, outcome.stderr
    assert outcome.logged[0] == "run:"
    assert f"reload:tls {portal}/fullchain.pem {portal}/privkey.pem" in outcome.logged
    assert outcome.logged[-1] == "reload:"  # back to ACME
    assert outcome.logged.count("reload:") == 1  # an unchanged serial never reloads
    assert "portal certificate " + "cd" * 32 + " applied" in outcome.stdout
    assert "withdrawn; back to ACME" in outcome.stdout


def test_a_half_written_portal_pair_is_ignored(bin_dir: Path, tmp_path: Path) -> None:
    portal = tmp_path / "portal-certs"
    portal.mkdir()
    (portal / "serial").write_text("ef" * 32 + "\n")  # no PEM files yet

    outcome = run(bin_dir, tmp_path, TLS_DOMAIN="afspraak.example.nl", ACME_EMAIL="x@y.nl")

    assert outcome.logged == ["run:"]


def test_the_operators_files_win_and_the_watcher_never_runs(bin_dir: Path, tmp_path: Path) -> None:
    portal = tmp_path / "portal-certs"
    portal.mkdir()
    write_pair(portal, "ab" * 32)
    (tmp_path / "own.crt").write_text("cert")
    (tmp_path / "own.key").write_text("key")

    outcome = run(
        bin_dir,
        tmp_path,
        run_seconds=2,
        TLS_DOMAIN="afspraak.example.nl",
        TLS_CERT_FILE=str(tmp_path / "own.crt"),
        TLS_KEY_FILE=str(tmp_path / "own.key"),
    )

    assert outcome.returncode == 0, outcome.stderr
    assert outcome.logged == [f"run:tls {tmp_path}/own.crt {tmp_path}/own.key"]


def test_one_file_of_the_operators_pair_stops_the_container(bin_dir: Path, tmp_path: Path) -> None:
    outcome = run(
        bin_dir, tmp_path, TLS_DOMAIN="afspraak.example.nl", TLS_CERT_FILE="/certs/only.pem"
    )

    assert outcome.returncode == 1
    assert "go together" in outcome.stderr
    assert outcome.logged == []
