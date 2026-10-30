"""The host updater script (ADR 0029, ADR 0048), run against fake docker, cosign, curl and flock."""

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
PROXY_IMAGE = f"{IMAGE}-proxy"
OLD, NEW = digest(IMAGE, "1.3.0"), digest(IMAGE, "1.4.0")
OLD_PROXY, NEW_PROXY = digest(PROXY_IMAGE, "1.3.0"), digest(PROXY_IMAGE, "1.4.0")
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

    def calls(self) -> list[tuple[str, str, str]]:
        """Return every tool call as (command line, images pinned at that moment: app, proxy)."""
        log = self.fakes / "calls.jsonl"
        if not log.exists():
            return []
        calls = []
        for line in log.read_text().splitlines():
            call = json.loads(line)
            args = call["args"]
            if args[:1] == ["compose"]:
                args = ["compose", *args[5:]]  # drop --project-directory and --env-file
            calls.append((" ".join([call["tool"], *args]), call["pinned"], call["proxy"]))
        return calls

    def commands(self) -> list[str]:
        return [command for command, _, _ in self.calls()]

    def index(self, command: str, pinned: str | None = None, proxy: str | None = None) -> int:
        """Return the position of the first call of ``command`` (with the pins, if given)."""
        for position, (line, image, proxy_image) in enumerate(self.calls()):
            if line == command and pinned in (None, image) and proxy in (None, proxy_image):
                return position
        raise AssertionError(f"not called: {command} (pinned {pinned}, proxy {proxy})")

    def pin(self, key: str) -> str | None:
        lines = self.pin_file.read_text().splitlines()
        return next((line.split("=", 1)[1] for line in lines if line.startswith(f"{key}=")), None)

    def pinned(self) -> str:
        pinned = self.pin("OPENPLANNER_IMAGE")
        assert pinned is not None
        return pinned

    def pinned_proxy(self) -> str | None:
        return self.pin("OPENPLANNER_PROXY_IMAGE")

    def with_proxy(self, proxy_image: str | None = OLD_PROXY) -> None:
        """Pin as the installer does for an installation with the HTTPS proxy (ADR 0064)."""
        pins = "OPENPLANNER_MODE=production\nOPENPLANNER_TLS=1\nCOMPOSE_PROFILES=tls\n"
        pins += f"OPENPLANNER_IMAGE={OLD}\n"
        if proxy_image:
            pins += f"OPENPLANNER_PROXY_IMAGE={proxy_image}\n"
        self.pin_file.write_text(pins)


def cli(*args: str) -> str:
    return " ".join(["docker compose exec -T web python -m app.cli", *args])


UP = "docker compose up -d web worker"
UP_WITH_PROXY = "docker compose up -d web worker proxy"


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
        "PREVIOUS_PROXY_IMAGE=\nNEWER_PROXY_IMAGE=\n"
    )
    # An installation without the HTTPS proxy never touches its image (ADR 0064 step 2).
    assert not any(PROXY_IMAGE in c for c in host.commands())
    assert host.pinned_proxy() is None


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


def test_a_healthy_update_is_checked_at_least_once_before_the_deadline(host: Host) -> None:
    config = host.root / "updater.env"
    config.write_text(config.read_text().replace("HEALTH_TIMEOUT=1", "HEALTH_TIMEOUT=0"))

    updated(host)

    assert reports(host)[-1] == cli("update-report", "7", "succeeded")


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


def test_an_update_keeps_the_installers_settings_in_the_pin_file(host: Host) -> None:
    # A pin file from before ADR 0064: the profile alone says the proxy runs, so its image is
    # pinned from now on (step 2).
    host.pin_file.write_text(
        f"OPENPLANNER_MODE=production\nCOMPOSE_PROFILES=tls\nOPENPLANNER_IMAGE={OLD}\n"
    )

    updated(host)

    assert host.pin_file.read_text().splitlines() == [
        "OPENPLANNER_MODE=production",
        "COMPOSE_PROFILES=tls",
        f"OPENPLANNER_IMAGE={NEW}",
        f"OPENPLANNER_PROXY_IMAGE={NEW_PROXY}",
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


def test_a_release_candidate_is_a_valid_target(host: Host) -> None:
    """ADR 0059: the rc channel updates to ``X.Y.Z-rc.N`` like to any release."""
    candidate = digest(IMAGE, "1.5.0-rc.3")
    host.given("claim", "9 update 1.5.0-rc.3 1.4.0\n")

    result = host.run()

    assert result.returncode == 0, result.stderr
    assert any(c.startswith(f"docker pull -q {IMAGE}:1.5.0-rc.3") for c in host.commands())
    assert host.index(UP, pinned=candidate) > host.index(cli("backup-db", "request-9"))
    assert reports(host)[-1] == cli("update-report", "9", "succeeded")


# --- the released proxy image (ADR 0064 step 2) ----------------------------------------------

VERIFY = (
    "cosign verify {} --certificate-identity-regexp ^https://github.com/acme/openplanner/"
    " --certificate-oidc-issuer https://token.actions.githubusercontent.com"
)


def test_an_update_switches_the_proxy_image_with_the_application(host: Host) -> None:
    host.with_proxy()

    updated(host)

    assert host.pinned_proxy() == NEW_PROXY
    assert host.index(VERIFY.format(NEW)) < host.index(cli("backup-db", "request-7"))
    assert host.index(VERIFY.format(NEW_PROXY)) < host.index(cli("backup-db", "request-7"))
    assert host.index(UP_WITH_PROXY, pinned=NEW, proxy=NEW_PROXY) > host.index(
        cli("backup-db", "request-7")
    )
    assert UP not in host.commands()
    assert "docker compose ps -q proxy" in host.commands()
    assert host.state_file.read_text().endswith(
        f"PREVIOUS_PROXY_IMAGE={OLD_PROXY}\nNEWER_PROXY_IMAGE={NEW_PROXY}\n"
    )
    assert host.pin_file.read_text().splitlines()[:3] == [
        "OPENPLANNER_MODE=production",
        "OPENPLANNER_TLS=1",
        "COMPOSE_PROFILES=tls",
    ]


def test_an_unsigned_proxy_image_leaves_both_images_as_they_are(host: Host) -> None:
    host.with_proxy()
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    host.given("fail", "cosign-proxy\n")

    host.run()

    assert (host.pinned(), host.pinned_proxy()) == (OLD, OLD_PROXY)
    assert reports(host)[-1] == cli("update-report", "7", "failed", "signature_invalid")
    assert UP_WITH_PROXY not in host.commands()
    assert ON not in host.commands()


def test_a_missing_proxy_image_is_image_not_found(host: Host) -> None:
    host.with_proxy()
    host.given("claim", "7 update 1.4.0 1.3.0\n")
    host.given("fail", "pull-proxy\n")

    host.run()

    assert (host.pinned(), host.pinned_proxy()) == (OLD, OLD_PROXY)
    assert reports(host)[-1] == cli("update-report", "7", "failed", "image_not_found")


def test_an_unhealthy_proxy_rolls_both_images_back(host: Host) -> None:
    host.with_proxy()
    host.given("unhealthy", f"{NEW_PROXY}\n")
    host.given("claim", "7 update 1.4.0 1.3.0\n")

    host.run()

    restore = "docker compose run --rm --no-deps -T web python -m app.cli restore-db request-7"
    assert host.index(UP_WITH_PROXY, proxy=NEW_PROXY) < host.index(restore)
    assert host.index(restore) < host.index(UP_WITH_PROXY, pinned=OLD, proxy=OLD_PROXY)
    assert (host.pinned(), host.pinned_proxy()) == (OLD, OLD_PROXY)
    assert reports(host)[-1] == cli("update-report", "7", "rolled_back", "health_check_failed")


def test_restore_returns_both_images(host: Host) -> None:
    host.with_proxy()
    updated(host)
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    result = host.run()

    assert result.returncode == 0
    downgrade = "docker compose run --rm --no-deps -T web alembic downgrade a1b2c3d4e5f6"
    assert host.index(downgrade, pinned=NEW, proxy=NEW_PROXY) < host.index(
        UP_WITH_PROXY, pinned=OLD, proxy=OLD_PROXY
    )
    assert (host.pinned(), host.pinned_proxy()) == (OLD, OLD_PROXY)
    assert reports(host)[-1] == cli("update-report", "8", "succeeded")


def test_the_first_update_pins_the_proxy_of_an_older_installation_and_restore_unpins_it(
    host: Host,
) -> None:
    # Before step 2 the proxy was built on the host; restoring returns to that local image.
    host.with_proxy(proxy_image=None)

    updated(host)
    assert host.pinned_proxy() == NEW_PROXY
    assert host.state_file.read_text().endswith(
        f"PREVIOUS_PROXY_IMAGE=\nNEWER_PROXY_IMAGE={NEW_PROXY}\n"
    )

    host.given("claim", "8 restore 1.3.0 1.4.0\n")
    assert host.run().returncode == 0
    assert (host.pinned(), host.pinned_proxy()) == (OLD, None)
    assert reports(host)[-1] == cli("update-report", "8", "succeeded")


def test_a_tampered_proxy_pin_is_never_executed(host: Host) -> None:
    host.with_proxy()
    updated(host)
    host.state_file.write_text(
        host.state_file.read_text().replace(OLD_PROXY, INJECTION % host.marker)
    )
    host.given("claim", "8 restore 1.3.0 1.4.0\n")

    host.run()

    assert not host.marker.exists()
    assert (host.pinned(), host.pinned_proxy()) == (NEW, NEW_PROXY)
    assert reports(host)[-1] == cli("update-report", "8", "failed", "updater_error")
