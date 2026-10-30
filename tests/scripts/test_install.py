"""The host installer (docs/install.md), run against fake docker, cosign, curl, sudo and systemd."""

import json
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.adapters.bootstrap import GENERATED
from app.domain.settings_registry import BY_KEY
from tests.fakes import updater_host
from tests.fakes.updater_host import digest

REPO = Path(__file__).parents[2]
BASH = shutil.which("bash") or "/bin/bash"
IMAGE = "ghcr.io/3corners-labs/openplanner"
RELEASE = digest(IMAGE, "1.4.0")
TOOLS = (
    *("docker", "cosign", "curl", "flock", "sleep", "systemctl", "systemd-analyze"),
    *("apt-get", "apt", "dpkg", "gpg", "sha256sum", "id", "usermod"),
)
# The only real programs the script sees, linked into its PATH: a runner's own docker or flock
# must never stand in for a fake the test removed.
SYSTEM = (
    *("awk", "base64", "cat", "chmod", "cp", "date", "dirname", "env", "grep", "head"),
    *("hostname", "install", "mkdir", "mktemp", "mv", "rm", "sed", "sort", "tail", "tr", "uniq"),
)
KEYS = tuple(BY_KEY[key].env for key in GENERATED)
PRODUCTION = {
    "TLS_DOMAIN": "afspraak.example.nl",
    "ACME_EMAIL": "ops@example.nl",
    "OIDC_DISCOVERY_URL": "https://idp.example.nl/.well-known/openid-configuration",
    "OIDC_CLIENT_SECRET": "client-secret-from-the-idp",
    "SMTP_HOST": "smtp.example.nl",
    "SMTP_USERNAME": "openplanner",
    "SMTP_PASSWORD": "pass word #1",
}


@dataclass
class Host:
    """A temporary host: the checkout in the operator's home, a staging root and fake tools."""

    root: Path

    @property
    def checkout(self) -> Path:
        return self.root / "home" / "operator" / "openplanner"

    @property
    def staged(self) -> Path:
        """Where the files outside the checkout land (``INSTALL_ROOT``)."""
        return self.root / "staged"

    @property
    def fakes(self) -> Path:
        return self.root / "fakes"

    def file(self, name: str) -> Path:
        return self.checkout / name

    def given(self, name: str, text: str) -> None:
        (self.fakes / name).write_text(text)

    def without(self, tool: str) -> None:
        (self.root / "bin" / tool).unlink()

    def run(self, *args: str, **settings: str) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": str(self.root / "bin"),
            "HOME": str(self.root / "home" / "operator"),
            "INSTALL_ROOT": str(self.staged),
            "HEALTH_TIMEOUT": "1",
            "FAKE_HOST_DIR": str(self.fakes),
            "FAKE_PIN_FILE": str(self.file(".env.update")),
            **settings,
        }
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [BASH, str(self.checkout / "scripts" / "install.sh"), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def commands(self) -> list[str]:
        log = self.fakes / "calls.jsonl"
        if not log.exists():
            return []
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return [" ".join([call["tool"], *call["args"]]) for call in calls]

    def ran(self, fragment: str) -> bool:
        return any(fragment in command for command in self.commands())

    def settings(self, name: str) -> dict[str, str]:
        """Read an env file as Compose does: the last occurrence wins, quotes removed."""
        values = {}
        for line in self.file(name).read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith("#"):
                values[key] = value.strip("'")
        return values


@pytest.fixture
def host(tmp_path: Path) -> Host:
    host = Host(tmp_path)
    for directory in ("bin", "fakes", "staged", "home/operator/.docker"):
        (tmp_path / directory).mkdir(parents=True)
    shutil.copytree(REPO / "scripts" / "updater", host.checkout / "scripts" / "updater")
    shutil.copy(REPO / "scripts" / "install.sh", host.checkout / "scripts" / "install.sh")
    host.file("docker-compose.yml").write_text("services: {}\n")
    (tmp_path / "home/operator/.docker/config.json").write_text('{"auths": {"ghcr.io": {}}}\n')
    fake = f'"{sys.executable}" "{updater_host.__file__}"'
    for tool in SYSTEM:
        found = shutil.which(tool)
        assert found, f"{tool} is needed to run the installer tests"
        (tmp_path / "bin" / tool).symlink_to(found)
    for tool in TOOLS:
        wrapper = tmp_path / "bin" / tool
        wrapper.write_text(f'#!/bin/sh\nexec {fake} {tool} "$@"\n')
        wrapper.chmod(0o755)
    sudo = tmp_path / "bin" / "sudo"
    sudo.write_text(f'#!/bin/sh\n{fake} sudo "$@" && exec "$@"\n')
    sudo.chmod(0o755)
    (host.staged / "etc").mkdir()
    (host.staged / "etc/os-release").write_text('ID=ubuntu\nVERSION_CODENAME="noble"\n')
    host.given("installed", "docker.io\ndocker-compose-v2\n")
    host.given("latest", "v1.4.0\n")
    return host


SECRETS = "secrets/secrets.env"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_missing_prerequisites_are_reported_together(host: Host) -> None:
    host.without("cosign")
    host.without("flock")
    host.given("fail", "docker-info\n")

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 1
    for problem in ("cosign is not installed", "flock is not installed", "docker group"):
        assert problem in result.stderr
    assert not host.file(".env").exists()
    assert not host.ran("pull")


def test_an_old_compose_is_refused(host: Host) -> None:
    host.given("compose-version", "2.20.2\n")

    result = host.run("--mode", "production", **PRODUCTION)

    assert result.returncode == 1
    assert "Docker Compose 2.20.2 is too old" in result.stderr


def test_production_install_from_scratch(host: Host) -> None:
    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    env = host.settings(".env")
    assert env["ENVIRONMENT"] == "prod"
    assert env["PUBLIC_BASE_URL"] == "https://afspraak.example.nl"
    assert env["SMTP_PASSWORD"] == "pass word #1"
    assert env["UPDATE_REPOSITORY"] == "3Corners-Labs/OpenPlanner"
    assert not set(KEYS) & set(env)
    keys = [host.settings(SECRETS)[key] for key in KEYS]
    assert len(set(keys)) == 3
    assert all(len(key) >= 32 for key in keys)
    assert mode(host.file(".env")) == 0o600
    assert mode(host.file(SECRETS)) == 0o600
    assert mode(host.file("secrets")) == 0o700
    assert host.settings("proxy.env") == {
        "TLS_DOMAIN": "afspraak.example.nl",
        "ACME_EMAIL": "ops@example.nl",
    }
    assert host.settings(".env.update") == {
        "OPENPLANNER_MODE": "production",
        "COMPOSE_PROFILES": "tls",
        "OPENPLANNER_IMAGE": RELEASE,
    }
    commands = host.commands()
    verified = commands.index(next(c for c in commands if c.startswith(f"cosign verify {RELEASE}")))
    assert verified < commands.index(next(c for c in commands if c.endswith(" up -d")))
    assert host.ran("sudo systemctl enable --now openplanner-updater.timer")


def test_the_updater_points_at_the_checkout_and_may_write_only_there(host: Host) -> None:
    assert host.run("--mode", "production", "--non-interactive", **PRODUCTION).returncode == 0

    config = host.staged / "etc/openplanner/updater.env"
    assert mode(config) == 0o600
    sourced = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            BASH,
            "-c",
            '. "$1" && printf "%s\\n" "$COMPOSE_DIR" "$CERT_IDENTITY_REGEXP"',
            "-",
            config,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.splitlines()
    assert sourced[0] == str(host.checkout)
    workflow = "3Corners-Labs/OpenPlanner/.github/workflows/main.yml"
    assert re.fullmatch(sourced[1], f"https://github.com/{workflow}@refs/tags/v1.4.0")
    assert not re.fullmatch(sourced[1], f"https://githubXcom/{workflow}@refs/tags/v1.4.0")
    drop_in = host.staged / "etc/systemd/system/openplanner-updater.service.d/override.conf"
    assert f"ReadWritePaths={host.checkout}\n" in drop_in.read_text()
    assert "ProtectHome=read-only\n" in drop_in.read_text()
    login = host.staged / "var/lib/openplanner-updater/.docker/config.json"
    assert mode(login) == 0o600


def test_an_existing_env_is_repaired_where_no_person_is_needed(host: Host) -> None:
    original = (
        "ENVIRONMENT=prod\n# mail\nSMTP_HOST=a\n"
        "PUBLIC_BASE_URL=https://afspraak.example.nl\nSMTP_HOST=b\n"
    )
    host.file(".env").write_text(original)
    host.file(".env").chmod(0o644)

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert host.file(".env").read_text().splitlines()[:4] == [
        "ENVIRONMENT=prod",
        "# mail",
        "PUBLIC_BASE_URL=https://afspraak.example.nl",
        "SMTP_HOST=b",
    ]
    for key in KEYS:
        assert len(host.settings(SECRETS)[key]) >= 32
    assert mode(host.file(".env")) == 0o600
    (backup,) = host.checkout.glob(".env.bak.*")
    assert backup.read_text() == original
    assert mode(backup) == 0o600


def test_keys_in_env_move_to_the_secrets_file_with_their_values(host: Host) -> None:
    original = (
        "ENVIRONMENT=prod\nPUBLIC_BASE_URL=https://afspraak.example.nl\n"
        "SETTINGS_ENCRYPTION_KEY='key-that-encrypted-the-database'\nSESSION_SECRET=session\n"
        "MANAGEMENT_TOKEN_KEY=tokens\n"
    )
    host.file(".env").write_text(original)

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert {key: host.settings(SECRETS)[key] for key in KEYS} == {
        "SETTINGS_ENCRYPTION_KEY": "key-that-encrypted-the-database",
        "MANAGEMENT_TOKEN_KEY": "tokens",
        "SESSION_SECRET": "session",
    }
    assert not set(KEYS) & set(host.settings(".env"))
    assert host.settings(".env")["PUBLIC_BASE_URL"] == "https://afspraak.example.nl"
    (backup,) = host.checkout.glob(".env.bak.*")
    assert backup.read_text() == original


def test_a_key_with_two_different_values_needs_a_person(host: Host) -> None:
    host.file(".env").write_text(
        "ENVIRONMENT=prod\nPUBLIC_BASE_URL=https://afspraak.example.nl\nSETTINGS_ENCRYPTION_KEY=a\n"
    )
    host.file("secrets").mkdir()
    host.file(SECRETS).write_text("SETTINGS_ENCRYPTION_KEY=b\n")

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 1
    assert "SETTINGS_ENCRYPTION_KEY has different values" in result.stderr
    assert host.file(SECRETS).read_text() == "SETTINGS_ENCRYPTION_KEY=b\n"
    assert "SETTINGS_ENCRYPTION_KEY=a" in host.file(".env").read_text()
    assert not host.ran("pull")


def test_an_existing_secrets_file_is_completed_never_rewritten(host: Host) -> None:
    host.file("secrets").mkdir(mode=0o755)
    host.file(SECRETS).write_text("# made by app.cli init\nSETTINGS_ENCRYPTION_KEY=kept\n")

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    lines = host.file(SECRETS).read_text().splitlines()
    assert lines[:2] == ["# made by app.cli init", "SETTINGS_ENCRYPTION_KEY=kept"]
    assert set(host.settings(SECRETS)) == set(KEYS)
    assert mode(host.file("secrets")) == 0o700


def test_a_key_from_a_file_is_not_generated(host: Host) -> None:
    host.file(".env").write_text(
        "ENVIRONMENT=prod\nPUBLIC_BASE_URL=https://afspraak.example.nl\n"
        "SETTINGS_ENCRYPTION_KEY_FILE=/run/secrets/settings_key\n"
    )

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert "SETTINGS_ENCRYPTION_KEY" not in host.settings(SECRETS)


def test_outside_production_an_existing_install_gets_only_a_session_secret(host: Host) -> None:
    host.file(".env").write_text("ENVIRONMENT=test\nPUBLIC_BASE_URL=http://127.0.0.1:8000\n")

    result = host.run("--mode", "lab", "--version", "1.4.0", "--non-interactive")

    assert result.returncode == 0, result.stderr
    # The others were generated in the data volume (ADR 0011); a new one would break links.
    assert set(host.settings(SECRETS)) == {"SESSION_SECRET"}


def test_what_needs_a_person_is_reported_and_nothing_starts(host: Host) -> None:
    host.file(".env").write_text("ENVIRONMENT=prod\nPUBLIC_BASE_URL=http://afspraak.example.nl\n")

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 1
    assert "PUBLIC_BASE_URL must start with https://" in result.stderr
    assert not host.ran("pull")
    assert not host.ran(" up -d")


def test_an_invalid_signature_changes_nothing(host: Host) -> None:
    host.given("fail", "cosign\n")

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 1
    assert "is not signed by a release" in result.stderr
    assert not host.file(".env.update").exists()
    assert not host.ran(" up -d")


def test_a_rerun_keeps_the_settings_and_the_pinned_release(host: Host) -> None:
    assert host.run("--mode", "production", "--non-interactive", **PRODUCTION).returncode == 0
    env, pin = host.file(".env").read_text(), host.file(".env.update").read_text()
    keys = host.file(SECRETS).read_text()
    host.given("latest", "v1.5.0\n")
    (host.fakes / "calls.jsonl").unlink()

    result = host.run()

    assert result.returncode == 0, result.stderr
    assert host.file(".env").read_text() == env
    assert host.file(".env.update").read_text() == pin
    assert host.file(SECRETS).read_text() == keys
    assert not host.ran("pull")


def test_lab_mode_runs_the_dev_services_and_removes_a_stray_proxy(host: Host) -> None:
    result = host.run("--mode", "lab", "--version", "1.4.0", "--non-interactive")

    assert result.returncode == 0, result.stderr
    assert host.settings(".env")["ENVIRONMENT"] == "test"
    assert host.settings(".env.update")["COMPOSE_PROFILES"] == "dev"
    assert not host.file("proxy.env").exists()
    assert host.ran("--profile tls rm --stop --force proxy")
    assert not host.ran("repos/3Corners-Labs/OpenPlanner/releases")


def test_host_tools_are_brought_to_their_newest_version_on_every_run(host: Host) -> None:
    assert host.run("--mode", "lab", "--version", "1.4.0").returncode == 0

    assert host.ran("apt-get -qq -o Dpkg::Options::=--force-confold update")
    assert host.ran("install -y --no-install-recommends ca-certificates curl gnupg util-linux")
    assert host.ran("install -y --no-install-recommends docker-compose-v2")
    assert not host.ran("--only-upgrade")


def test_an_engine_update_waits_for_the_flag_as_it_restarts_every_container(host: Host) -> None:
    host.given("upgradable", "containerd\ncurl\n")

    waiting = host.run("--mode", "lab", "--version", "1.4.0")
    assert waiting.returncode == 0, waiting.stderr
    assert "updates for Docker Engine are waiting (containerd)" in waiting.stderr
    assert not host.ran("--only-upgrade")

    upgraded = host.run("--upgrade-docker")
    assert upgraded.returncode == 0, upgraded.stderr
    assert host.ran("install --only-upgrade -y docker.io containerd runc")


def test_an_old_cosign_is_replaced_by_the_verified_latest_release(host: Host) -> None:
    host.given("cosign-version", "v2.6.1\n")

    assert host.run("--mode", "lab", "--version", "1.4.0").returncode == 0

    assert host.ran("releases/download/v3.1.4/cosign_3.1.4_amd64.deb")
    installs = [c for c in host.commands() if c.startswith("apt-get") and c.endswith(".deb")]
    assert len(installs) == 1
    assert installs[0].endswith("/cosign_3.1.4_amd64.deb")


def test_a_cosign_package_with_a_wrong_checksum_is_never_installed(host: Host) -> None:
    host.given("cosign-version", "v2.6.1\n")
    host.given("fail", "checksum\n")

    result = host.run("--mode", "lab", "--version", "1.4.0")

    assert result.returncode == 1
    assert "checksum of cosign_3.1.4_amd64.deb does not match" in result.stderr
    assert not [c for c in host.commands() if c.startswith("apt-get") and c.endswith(".deb")]


def test_a_missing_docker_comes_from_dockers_repository_with_a_checked_key(host: Host) -> None:
    host.without("docker")
    host.given("installed", "")

    host.run("--mode", "lab", "--version", "1.4.0")

    sources = host.staged / "etc/apt/sources.list.d/docker.list"
    assert "https://download.docker.com/linux/ubuntu noble stable" in sources.read_text()
    engine = "docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    assert host.ran(f"install -y {engine}")


def test_a_foreign_docker_key_stops_the_installation(host: Host) -> None:
    host.without("docker")
    host.given("docker-key", "0000000000000000000000000000000000000000\n")

    result = host.run("--mode", "lab", "--version", "1.4.0")

    assert result.returncode == 1
    assert "expected 9DC858229FC7DD38854AE2D88D81803C0EBFCD88" in result.stderr
    assert not (host.staged / "etc/apt/sources.list.d/docker.list").exists()


def test_the_installer_generates_the_keys_app_cli_init_generates() -> None:
    script = (REPO / "scripts" / "install.sh").read_text()

    (keys,) = re.findall(r"^BOOTSTRAP_KEYS=\((.*)\)$", script, flags=re.MULTILINE)
    assert sorted(keys.split()) == sorted(KEYS)


# Trust by installer mode (ADR 0065): the channel itself is chosen in the portal.


def _identity(host: Host) -> str:
    text = (host.staged / "etc/openplanner/updater.env").read_text()
    return next(line for line in text.splitlines() if line.startswith("CERT_IDENTITY_REGEXP="))[
        len("CERT_IDENTITY_REGEXP=") :
    ].strip("'")


WORKFLOW = "https://github.com/3Corners-Labs/OpenPlanner/.github/workflows/main.yml"


def test_a_lab_host_trusts_images_signed_on_main_and_may_pin_a_candidate(host: Host) -> None:
    result = host.run("--mode", "lab", "--version", "1.5.0-rc.3", "--non-interactive")

    assert result.returncode == 0, result.stderr
    assert host.ran(f"pull -q {IMAGE}:1.5.0-rc.3")
    assert host.settings(".env.update")["OPENPLANNER_IMAGE"] == digest(IMAGE, "1.5.0-rc.3")
    assert "UPDATE_CHANNEL" not in host.settings(".env")  # the portal's, not the host's
    identity = _identity(host)
    assert re.fullmatch(identity, f"{WORKFLOW}@refs/heads/main")
    assert re.fullmatch(identity, f"{WORKFLOW}@refs/tags/v1.5.0")
    assert not re.fullmatch(identity, f"{WORKFLOW}@refs/heads/feature")


def test_a_production_host_trusts_release_tags_only_and_refuses_a_candidate(host: Host) -> None:
    assert host.run("--mode", "production", "--non-interactive", **PRODUCTION).returncode == 0
    assert not re.fullmatch(_identity(host), f"{WORKFLOW}@refs/heads/main")

    result = host.run("--version", "1.5.0-rc.3", "--non-interactive", **PRODUCTION)

    assert result.returncode != 0
    assert "production runs releases only" in result.stderr
    assert not host.ran(f"pull -q {IMAGE}:1.5.0-rc.3")


def test_the_channel_flag_is_gone(host: Host) -> None:
    result = host.run("--mode", "lab", "--channel", "rc", "--non-interactive")

    assert result.returncode != 0
    assert "/admin/updates" in result.stderr
    assert not host.file(".env").exists()
