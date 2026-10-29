"""The host updater script (ADR 0029, ADR 0048), run against fake docker, cosign, curl and flock."""

import json
import os
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
        return self.pin_file.read_text().removeprefix("OPENPLANNER_IMAGE=").strip()


def cli(*args: str) -> str:
    return " ".join(["docker compose exec -T web python -m app.cli", *args])


UP = "docker compose up -d web worker"


def reports(host: Host) -> list[str]:
    """The outcomes the updater reported, in order (maintenance may be switched after them)."""
    return [c for c in host.commands() if c.startswith(cli("update-report"))]


def steps(host: Host, request_id: str) -> list[str]:
    """Return the steps the updater reported for ``request_id``, in order."""
    prefix = cli("update-progress", request_id, "")
    return [c.removeprefix(prefix) for c in host.commands() if c.startswith(prefix)]


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
    assert reports(host)[-1] == cli("update-report", "7", "succeeded")
    assert host.state_file.read_text() == (
        f"PREVIOUS_IMAGE={OLD}\nPREVIOUS_REVISION=a1b2c3d4e5f6\n"
        f"PREVIOUS_VERSION=1.3.0\nNEWER_IMAGE={NEW}\n"
    )


def test_invalid_signature_leaves_the_running_version(host: Host) -> None:
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    host.given("fail", "cosign\n")

    host.run()

    assert host.pinned() == OLD
    assert reports(host)[-1] == cli("update-report", "7", "failed", "signature_invalid")
    assert UP not in host.commands()
    assert cli("backup-db", "request-7") not in host.commands()


def test_unhealthy_update_restores_the_backup_and_the_previous_digest(host: Host) -> None:
    host.given("unhealthy", f"{NEW}\n")
    host.given("claim", "7 update 1.4.0 1.3.0\n")

    host.run()

    restore = "docker compose run --rm --no-deps -T web python -m app.cli restore-db request-7"
    assert host.index(UP, pinned=NEW) < host.index(restore) < host.index(UP, pinned=OLD)
    assert host.pinned() == OLD
    assert reports(host)[-1] == cli("update-report", "7", "rolled_back", "health_check_failed")
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
    assert reports(host)[-1] == cli("update-report", "8", "succeeded")
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
    assert reports(host)[-1] == cli("update-report", "7", "failed", "backup_failed")
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
    assert reports(host)[-1] == cli("update-report", "8", "failed", "updater_error")


def test_the_state_file_is_never_sourced(host: Host) -> None:
    updated(host)
    with host.state_file.open("a") as state:
        state.write(f"touch {host.marker}\n")
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    host.run()

    assert not host.marker.exists()
    assert host.pinned() == OLD
    assert reports(host)[-1] == cli("update-report", "8", "succeeded")


def test_update_reports_each_step_before_taking_it(host: Host) -> None:
    updated(host)

    assert steps(host, "7") == ["pulling", "verifying", "backing_up", "starting"]
    assert host.index(cli("update-progress", "7", "verifying")) < host.index(
        f"cosign verify {NEW} --certificate-identity-regexp ^https://github.com/acme/openplanner/"
        " --certificate-oidc-issuer https://token.actions.githubusercontent.com"
    )
    assert host.index(cli("update-progress", "7", "backing_up")) < host.index(
        cli("backup-db", "request-7")
    )
    assert host.index(cli("update-progress", "7", "starting")) < host.index(UP, pinned=NEW)


def test_unhealthy_update_reports_rolling_back(host: Host) -> None:
    host.given("unhealthy", f"{NEW}\n")
    host.given("claim", "7 update 1.4.0 1.3.0\n")

    host.run()

    assert steps(host, "7") == ["pulling", "verifying", "backing_up", "starting", "rolling_back"]


def test_restore_reports_its_steps(host: Host) -> None:
    updated(host)
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    host.run()

    assert steps(host, "8") == ["backing_up", "migrating", "starting"]
    assert host.index(UP, pinned=OLD) < host.index(cli("update-progress", "8", "starting"))


def test_a_version_without_steps_does_not_stop_the_update(host: Host) -> None:
    host.given("fail", "update-progress\n")

    updated(host)

    assert reports(host)[-1] == cli("update-report", "7", "succeeded")


# --- maintenance mode around the backup (ADR 0038 §5, MM-4) ---------------------------------

ON = cli("maintenance", "on", "--reason", "update", "--by", "host-updater")
ON_RESTORE = cli("maintenance", "on", "--reason", "restore", "--by", "host-updater")
OFF = cli("maintenance", "off", "--reason", "update", "--by", "host-updater")


def test_maintenance_is_on_before_the_backup_and_off_after_success(host: Host) -> None:
    updated(host)

    assert host.index(ON) < host.index(cli("backup-db", "request-7"))
    assert host.index(cli("update-report", "7", "succeeded")) < host.index(OFF, pinned=NEW)
    assert host.commands()[-1] == OFF


def test_maintenance_is_off_after_a_rollback(host: Host) -> None:
    host.given("unhealthy", f"{NEW}\n")
    host.given("claim", "7 update 1.4.0 1.3.0\n")

    host.run()

    assert host.index(ON) < host.index(cli("backup-db", "request-7"))
    # The backup was taken with maintenance on, so the restored database has it on too.
    assert host.index(UP, pinned=OLD) < host.index(OFF, pinned=OLD)
    assert host.commands()[-1] == OFF


def test_maintenance_is_on_around_a_restore_and_off_after_it(host: Host) -> None:
    updated(host)
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    host.run()

    assert host.index(ON_RESTORE) < host.index(cli("backup-db", "request-8-restore"))
    assert host.commands()[-1] == OFF
    assert host.index(OFF, pinned=OLD) > host.index(UP, pinned=OLD)


def test_a_failed_update_leaves_maintenance_on(host: Host) -> None:
    host.given("unhealthy", f"{NEW}\n{OLD}\n")
    host.given("claim", "7 update 1.4.0 1.3.0\n")

    host.run()

    assert reports(host)[-1] == cli("update-report", "7", "failed", "restore_failed")
    assert ON in host.commands()
    assert OFF not in host.commands()


def test_a_failed_backup_changes_nothing_and_switches_maintenance_off(host: Host) -> None:
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    host.given("fail", "backup-db\n")

    host.run()

    assert host.pinned() == OLD
    assert host.commands()[-2:] == [cli("update-report", "7", "failed", "backup_failed"), OFF]


def test_no_maintenance_before_the_signature_is_verified(host: Host) -> None:
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    host.given("fail", "cosign\n")

    host.run()

    assert ON not in host.commands()


def test_an_old_version_without_maintenance_does_not_stop_the_update(host: Host) -> None:
    host.given("fail", "maintenance\n")

    updated(host)

    assert host.commands()[-1] == OFF
    assert host.commands()[-2] == cli("update-report", "7", "succeeded")
