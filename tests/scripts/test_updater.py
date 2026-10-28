"""The host updater script (ADR 0029), run against fake docker, cosign, curl and flock."""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fakes import updater_host
from tests.fakes.updater_host import digest

SCRIPT = Path(__file__).parents[2] / "scripts" / "updater" / "openplanner-update.sh"
BASH = shutil.which("bash") or "/bin/bash"
IMAGE = "ghcr.io/acme/openplanner"
OLD, NEW = digest(IMAGE, "1.3.0"), digest(IMAGE, "1.4.0")
INJECTION = "$(touch${IFS}%s)"


@dataclass
class Host:
    """A temporary host: compose directory, updater state, fake tools and their call log."""

    root: Path

    @property
    def fakes(self) -> Path:
        return self.root / "fakes"

    @property
    def pin_file(self) -> Path:
        return self.root / "compose" / ".env.update"

    @property
    def state_file(self) -> Path:
        return self.root / "state" / "previous.env"

    @property
    def marker(self) -> Path:
        """A file that only exists if text from the container was executed as shell."""
        return self.root / "injected"

    def given(self, name: str, text: str) -> None:
        (self.fakes / name).write_text(text)

    def run(self) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "OPENPLANNER_UPDATER_CONFIG": str(self.root / "updater.env"),
            "FAKE_HOST_DIR": str(self.fakes),
            "FAKE_PIN_FILE": str(self.pin_file),
        }
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False
        )

    def calls(self) -> list[tuple[str, str]]:
        """Return every tool call as (command line, image pinned at that moment)."""
        log = self.fakes / "calls.jsonl"
        if not log.exists():
            return []
        calls = []
        for line in log.read_text().splitlines():
            call = json.loads(line)
            args = call["args"]
            if args[:1] == ["compose"]:
                args = ["compose", *args[5:]]  # drop --project-directory and --env-file
            calls.append((" ".join([call["tool"], *args]), call["pinned"]))
        return calls

    def commands(self) -> list[str]:
        return [command for command, _ in self.calls()]

    def index(self, command: str, pinned: str | None = None) -> int:
        """Return the position of the first call of ``command`` (with ``pinned``, if given)."""
        for position, (line, image) in enumerate(self.calls()):
            if line == command and pinned in (None, image):
                return position
        raise AssertionError(f"not called: {command} (pinned {pinned})")

    def pinned(self) -> str:
        lines = self.pin_file.read_text().splitlines()
        return next(line for line in lines if line.startswith("OPENPLANNER_IMAGE=")).split("=", 1)[
            1
        ]


def cli(*args: str) -> str:
    return " ".join(["docker compose exec -T web python -m app.cli", *args])


UP = "docker compose up -d web worker"


@pytest.fixture
def host(tmp_path: Path) -> Host:
    host = Host(tmp_path)
    for directory in ("bin", "fakes", "compose", "state"):
        (tmp_path / directory).mkdir()
    for tool in ("docker", "cosign", "curl", "flock", "sleep"):
        wrapper = tmp_path / "bin" / tool
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{updater_host.__file__}" {tool} "$@"\n'
        )
        wrapper.chmod(0o755)
    (tmp_path / "updater.env").write_text(
        f"COMPOSE_DIR={tmp_path / 'compose'}\n"
        f"UPDATE_IMAGE={IMAGE}\n"
        "CERT_IDENTITY_REGEXP=^https://github.com/acme/openplanner/\n"
        "HEALTH_TIMEOUT=1\n"
        f"STATE_DIR={tmp_path / 'state'}\n"
    )
    host.pin_file.write_text(f"OPENPLANNER_IMAGE={OLD}\n")
    host.given("claim", "")
    host.given("revision", "a1b2c3d4e5f6")
    return host


def updated(host: Host) -> None:
    """Carry out a successful update from 1.3.0 to 1.4.0 as request 7."""
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    assert host.run().returncode == 0
    assert host.pinned() == NEW


def test_no_request_does_nothing(host: Host) -> None:
    result = host.run()

    assert result.returncode == 0
    assert host.commands() == ["flock -n 9", cli("backup-db", "--prune"), cli("update-claim")]
    assert host.pinned() == OLD


def test_update_pins_the_verified_digest_after_a_backup(host: Host) -> None:
    updated(host)

    verify = (
        f"cosign verify {NEW} --certificate-identity-regexp ^https://github.com/acme/openplanner/"
        " --certificate-oidc-issuer https://token.actions.githubusercontent.com"
    )
    assert host.index(verify) < host.index(cli("backup-db", "request-7"))
    assert host.index(cli("backup-db", "request-7")) < host.index(UP, pinned=NEW)
    assert host.commands()[-1] == cli("update-report", "7", "succeeded")
    assert host.state_file.read_text() == (
        f"PREVIOUS_IMAGE={OLD}\nPREVIOUS_REVISION=a1b2c3d4e5f6\n"
        f"PREVIOUS_VERSION=1.3.0\nNEWER_IMAGE={NEW}\n"
    )


def test_invalid_signature_leaves_the_running_version(host: Host) -> None:
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    host.given("fail", "cosign\n")

    host.run()

    assert host.pinned() == OLD
    assert host.commands()[-1] == cli("update-report", "7", "failed", "signature_invalid")
    assert UP not in host.commands()
    assert cli("backup-db", "request-7") not in host.commands()


def test_unhealthy_update_restores_the_backup_and_the_previous_digest(host: Host) -> None:
    host.given("unhealthy", f"{NEW}\n")
    host.given("claim", "7 update 1.4.0 1.3.0\n")

    host.run()

    restore = "docker compose run --rm --no-deps -T web python -m app.cli restore-db request-7"
    assert host.index(UP, pinned=NEW) < host.index(restore) < host.index(UP, pinned=OLD)
    assert host.pinned() == OLD
    assert host.commands()[-1] == cli("update-report", "7", "rolled_back", "health_check_failed")
    assert not host.state_file.exists()


def test_restore_downgrades_the_schema_then_runs_the_previous_digest(host: Host) -> None:
    updated(host)
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    result = host.run()

    assert result.returncode == 0
    downgrade = "docker compose run --rm --no-deps -T web alembic downgrade a1b2c3d4e5f6"
    assert host.index(cli("backup-db", "request-8-restore")) < host.index(downgrade, pinned=NEW)
    assert host.index(downgrade) < host.index(UP, pinned=OLD)
    assert host.pinned() == OLD
    assert host.commands()[-1] == cli("update-report", "8", "succeeded")
    assert not host.state_file.exists()


@pytest.mark.parametrize(
    "claim",
    [
        "{x} update 1.4.0 1.3.0",
        "7 update {x} 1.3.0",
        "7 update 1.4.0 {x}",
        "7 {x} 1.4.0 1.3.0",
        "7 update `touch${{IFS}}{m}` 1.3.0",
    ],
)
def test_a_claim_is_never_executed(host: Host, claim: str) -> None:
    host.given("claim", claim.format(x=INJECTION % host.marker, m=host.marker) + "\n")

    host.run()

    assert not host.marker.exists()
    assert host.pinned() == OLD
    assert UP not in host.commands()


def test_an_alembic_revision_is_never_executed(host: Host) -> None:
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    host.given("revision", INJECTION % host.marker)

    host.run()

    assert not host.marker.exists()
    assert host.pinned() == OLD
    assert host.commands()[-1] == cli("update-report", "7", "failed", "backup_failed")
    assert not host.state_file.exists()


def test_a_tampered_state_file_is_never_executed(host: Host) -> None:
    updated(host)
    host.state_file.write_text(
        host.state_file.read_text().replace("a1b2c3d4e5f6", INJECTION % host.marker)
    )
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    host.run()

    assert not host.marker.exists()
    assert host.pinned() == NEW
    assert host.commands()[-1] == cli("update-report", "8", "failed", "updater_error")


def test_the_state_file_is_never_sourced(host: Host) -> None:
    updated(host)
    with host.state_file.open("a") as state:
        state.write(f"touch {host.marker}\n")
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    host.run()

    assert not host.marker.exists()
    assert host.pinned() == OLD
    assert host.commands()[-1] == cli("update-report", "8", "succeeded")


def test_an_update_keeps_the_installers_settings_in_the_pin_file(host: Host) -> None:
    host.pin_file.write_text(
        f"OPENPLANNER_MODE=production\nCOMPOSE_PROFILES=tls\nOPENPLANNER_IMAGE={OLD}\n"
    )

    updated(host)

    assert host.pin_file.read_text().splitlines() == [
        "OPENPLANNER_MODE=production",
        "COMPOSE_PROFILES=tls",
        f"OPENPLANNER_IMAGE={NEW}",
    ]


def test_the_example_config_keeps_the_signature_identity_strict(tmp_path: Path) -> None:
    example = SCRIPT.with_name("updater.env.example").read_text()
    config = tmp_path / "updater.env"
    config.write_text(example.replace("<owner>", "acme").replace("<repo>", "openplanner"))

    sourced = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [BASH, "-c", '. "$1" && printf %s "$CERT_IDENTITY_REGEXP"', "bash", str(config)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout

    workflow = "acme/openplanner/.github/workflows/main.yml"
    assert re.fullmatch(sourced, f"https://github.com/{workflow}@refs/tags/v1.4.0")
    assert not re.fullmatch(sourced, f"https://githubXcom/{workflow}@refs/tags/v1.4.0")
