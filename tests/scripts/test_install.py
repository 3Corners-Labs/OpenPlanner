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
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from app.adapters.bootstrap import GENERATED
from app.config import Settings
from app.domain.settings_registry import BY_KEY
from app.domain.web_transport import BUNDLED_IDP_DISCOVERY_URL, BUNDLED_IDP_UPSTREAM
from tests.fakes import updater_host
from tests.fakes.updater_host import digest
from tests.support.compose import compose_file, fixed_address

REPO = Path(__file__).parents[2]
BASH = shutil.which("bash") or "/bin/bash"
IMAGE = "ghcr.io/3corners-labs/openplanner"
PROXY_IMAGE = f"{IMAGE}-proxy"
RELEASE = digest(IMAGE, "1.4.0")
PROXY_RELEASE = digest(PROXY_IMAGE, "1.4.0")
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
IDP_UPSTREAM = BUNDLED_IDP_UPSTREAM
"""Where the proxy reaches the bundled provider: its fixed address (ADR 0125 step 2)."""
IDP_DB_ADDRESS = str(fixed_address("idp-db", "idp-store"))
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
            # Whole seconds: with "1" the health loop of install.sh is skipped when the clock
            # ticks over between computing the deadline and testing it, which a loaded machine
            # makes likely enough. The fake stack is healthy at once, so a long limit costs nothing.
            "HEALTH_TIMEOUT": "30",
            "FAKE_HOST_DIR": str(self.fakes),
            "FAKE_PIN_FILE": str(self.file(".env.update")),
            # A kernel with IPv6, whatever the machine that runs the tests has.
            "KERNEL_IPV6": str(self.root / "proc-sys-net-ipv6"),
            **settings,
        }
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [BASH, str(self.checkout / "scripts" / "install.sh"), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            # Only a guard against a hang. The script makes about forty fake tool calls, each a
            # Python start-up; a busy machine (parallel test workers) is five times slower.
            timeout=300,
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
    for directory in ("bin", "fakes", "staged", "home/operator/.docker", "proc-sys-net-ipv6"):
        (tmp_path / directory).mkdir(parents=True)
    shutil.copytree(REPO / "scripts" / "updater", host.checkout / "scripts" / "updater")
    shutil.copy(REPO / "scripts" / "install.sh", host.checkout / "scripts" / "install.sh")
    # The bundled identity provider is rendered on the host (ADR 0106 step 5), at the fixed
    # addresses both scripts read (ADR 0125 step 2).
    for script in ("render-keycloak-realm.sh", "fixed-addresses.sh"):
        shutil.copy(REPO / "scripts" / script, host.checkout / "scripts" / script)
    (host.checkout / "deploy" / "keycloak").mkdir(parents=True)
    shutil.copy(
        REPO / "deploy" / "keycloak" / "realm.template.json",
        host.checkout / "deploy" / "keycloak" / "realm.template.json",
    )
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


@pytest.mark.parametrize(
    ("engine", "daemon", "warning"),
    [
        ("26.1.3", None, "Docker Engine 26.1.3 forwards visitors over IPv6 without NAT"),
        ("29.1.3", '{"mtu": 1450, "ip6tables" : false}\n', "daemon.json switches ip6tables off"),
        ("29.1.3", '{"mtu": 1450}\n', None),
        ("27.0.1", None, None),
    ],
)
def test_a_docker_that_hides_the_address_of_visitors_over_ipv6_is_named(
    host: Host, engine: str, daemon: str | None, warning: str | None
) -> None:
    """Without NAT for IPv6 the host works, but visitors over IPv6 share one rate limit."""
    host.given("engine-version", f"{engine}\n")
    if daemon is not None:
        (host.staged / "etc/docker").mkdir(parents=True)
        (host.staged / "etc/docker/daemon.json").write_text(daemon)

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    if warning:
        assert warning in result.stderr
        assert "share one address in the rate limits" in result.stderr
    else:
        assert "over IPv6" not in result.stderr


def test_a_kernel_without_ipv6_keeps_the_edge_network_ipv4_only(host: Host) -> None:
    """A kernel booted with ipv6.disable=1 cannot make the dual-stack edge network."""
    host.given("edge-ipv6", "false\n")
    host.given("engine-version", "26.1.3\n")  # no IPv6 visitors to warn about

    result = host.run(
        "--mode",
        "production",
        "--non-interactive",
        KERNEL_IPV6=str(host.root / "no-such-directory"),
        **PRODUCTION,
    )

    assert result.returncode == 0, result.stderr
    assert host.settings(".env.update")["OPENPLANNER_EDGE_IPV6"] == "false"
    assert "the kernel has no IPv6" in result.stdout
    assert "over IPv6" not in result.stderr
    assert not any(command.endswith(" down") for command in host.commands())


def test_an_edge_network_from_before_ipv6_is_recreated_before_the_start(host: Host) -> None:
    """Compose 2.29 kept a network whose settings changed (2.31 recreates it): the script does."""
    host.given("edge-ipv6", "false\n")

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert "OPENPLANNER_EDGE_IPV6" not in host.settings(".env.update")
    commands = host.commands()
    down = [i for i, command in enumerate(commands) if command.endswith(" down")]
    started = commands.index(next(c for c in commands if c.endswith(" up -d")))
    assert len(down) == 1
    assert down[0] < started
    assert "recreating it" in result.stdout
    assert not any(" down -v" in command for command in commands)  # the volumes stay


def test_an_edge_network_that_fits_the_host_is_left_alone(host: Host) -> None:
    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert host.ran("network inspect -f {{.EnableIPv6}} openplanner_edge")
    assert not any(command.endswith(" down") for command in host.commands())


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
        "OPENPLANNER_TLS": "1",
        "OPENPLANNER_IDP": "own",  # a login named in the environment stays (ADR 0106 point 4)
        "COMPOSE_PROFILES": "tls",
        "OPENPLANNER_IMAGE": RELEASE,
        "OPENPLANNER_PROXY_IMAGE": PROXY_RELEASE,
    }
    commands = host.commands()
    started = commands.index(next(c for c in commands if c.endswith(" up -d")))
    # ADR 0064 step 2: both images are verified before anything starts; no `docker build`.
    for image in (RELEASE, PROXY_RELEASE):
        verified = commands.index(
            next(c for c in commands if c.startswith(f"cosign verify {image}"))
        )
        assert verified < started
    assert not host.ran("docker build")
    assert host.ran("sudo systemctl enable --now openplanner-updater.timer")


def test_the_updater_points_at_the_checkout_and_may_write_only_there(host: Host) -> None:
    assert host.run("--mode", "production", "--non-interactive", **PRODUCTION).returncode == 0

    config = host.staged / "etc/openplanner/updater.env"
    assert mode(config) == 0o600
    sourced = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            BASH,
            "-c",
            '. "$1" && printf "%s\\n" "$COMPOSE_DIR" "$CERT_IDENTITY_REGEXP" "$UPDATE_PROXY_IMAGE"',
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
    assert sourced[2] == PROXY_IMAGE
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
    # The login is the bundled provider's, rendered into secrets/keycloak-app.env (ADR 0106 step
    # 5), not asked and not copied into .env. Keycloak announces KC_HOSTNAME, not the address the
    # web container fetches it on (ADR 0097).
    assert "OIDC_DISCOVERY_URL" not in host.settings(".env")
    login = host.settings("secrets/keycloak-app.env")
    assert login["OIDC_ISSUER"] == "http://127.0.0.1:8080/realms/openplanner"
    assert login["OIDC_DISCOVERY_URL"].startswith(f"http://{IDP_UPSTREAM}/realms/openplanner/")
    assert host.settings(".env.update")["COMPOSE_PROFILES"] == "idp,mailpit"
    assert host.settings(".env.update")["OPENPLANNER_IDP"] == "bundled"
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


def test_test_mode_runs_the_https_proxy_by_default(host: Host) -> None:
    # ADR 0064: HTTPS is the standard shape; only --no-tls leaves it to a proxy of your own.
    result = host.run(
        "--mode",
        "test",
        "--version",
        "1.4.0",
        "--non-interactive",
        TLS_DOMAIN="test.example.nl",
        ACME_EMAIL="ops@example.nl",
    )

    assert result.returncode == 0, result.stderr
    env = host.settings(".env")
    assert env["ENVIRONMENT"] == "test"
    assert env["PUBLIC_BASE_URL"] == "https://test.example.nl"
    assert host.settings("proxy.env") == {
        "TLS_DOMAIN": "test.example.nl",
        "ACME_EMAIL": "ops@example.nl",
        "IDP_UPSTREAM": IDP_UPSTREAM,  # the bundled Keycloak under /idp (ADR 0112, 0125)
    }
    assert host.settings(".env.update")["COMPOSE_PROFILES"] == "idp,tls"
    assert host.file("certs").is_dir()
    assert not host.ran("--profile tls rm --stop --force proxy")


def test_no_tls_leaves_https_to_a_proxy_of_your_own(host: Host) -> None:
    settings = {k: v for k, v in PRODUCTION.items() if k not in ("TLS_DOMAIN", "ACME_EMAIL")}
    result = host.run(
        "--mode",
        "production",
        "--no-tls",
        "--non-interactive",
        PUBLIC_BASE_URL="https://afspraak.example.nl",
        FORWARDED_ALLOW_IPS="10.0.0.5",
        **settings,
    )

    assert result.returncode == 0, result.stderr
    env = host.settings(".env")
    assert env["PUBLIC_BASE_URL"] == "https://afspraak.example.nl"
    assert env["FORWARDED_ALLOW_IPS"] == "10.0.0.5"
    assert not host.file("proxy.env").exists()
    assert "COMPOSE_PROFILES" not in host.settings(".env.update")
    # Without the proxy its image is neither pulled nor pinned (ADR 0064 step 2).
    assert "OPENPLANNER_PROXY_IMAGE" not in host.settings(".env.update")
    assert not host.ran(PROXY_IMAGE)
    assert host.ran("--profile tls rm --stop --force proxy")
    # A rerun remembers the choice through the pin file.
    again = host.run("--non-interactive")
    assert again.returncode == 0, again.stderr
    assert not host.file("proxy.env").exists()


def test_a_lab_opts_in_to_https_with_a_domain(host: Host) -> None:
    result = host.run(
        "--mode",
        "lab",
        "--version",
        "1.4.0",
        "--tls-domain",
        "docker01.example.nl",
        "--acme-staging",
        "--non-interactive",
        ACME_EMAIL="ops@example.nl",
    )

    assert result.returncode == 0, result.stderr
    assert host.settings(".env")["PUBLIC_BASE_URL"] == "https://docker01.example.nl"
    assert host.settings(".env.update")["COMPOSE_PROFILES"] == "idp,mailpit,tls"
    assert host.settings("proxy.env") == {
        "TLS_DOMAIN": "docker01.example.nl",
        "ACME_EMAIL": "ops@example.nl",
        "ACME_CA": "https://acme-staging-v02.api.letsencrypt.org/directory",
        "IDP_UPSTREAM": IDP_UPSTREAM,
    }


def test_an_own_certificate_replaces_acme_and_needs_both_files(host: Host) -> None:
    settings = {k: v for k, v in PRODUCTION.items() if k != "ACME_EMAIL"}
    missing = host.run("--mode", "production", "--own-certificate", "--non-interactive", **settings)
    assert missing.returncode == 1
    assert "fullchain.pem and privkey.pem" in missing.stderr
    assert not host.file("proxy.env").exists()

    host.file("certs").mkdir(exist_ok=True)
    host.file("certs/fullchain.pem").write_text("cert")
    host.file("certs/privkey.pem").write_text("key")
    result = host.run("--mode", "production", "--own-certificate", "--non-interactive", **settings)

    assert result.returncode == 0, result.stderr
    assert host.settings("proxy.env") == {
        "TLS_DOMAIN": "afspraak.example.nl",
        "TLS_CERT_FILE": "/certs/fullchain.pem",
        "TLS_KEY_FILE": "/certs/privkey.pem",
    }
    assert host.settings(".env.update")["COMPOSE_PROFILES"] == "tls"
    # ADR 0070: the portal's source setting is locked, so the page says the host manages it.
    assert host.settings(".env")["TLS_CERTIFICATE_SOURCE"] == "own"

    again = host.run("--mode", "production", "--own-certificate", "--non-interactive", **settings)

    assert again.returncode == 0, again.stderr
    assert host.file(".env").read_text().count("TLS_CERTIFICATE_SOURCE=") == 1


def test_own_certificate_without_the_proxy_is_refused(host: Host) -> None:
    result = host.run("--mode", "production", "--no-tls", "--own-certificate", "--non-interactive")

    assert result.returncode == 1
    assert "--own-certificate needs the HTTPS proxy" in result.stderr


def test_the_internal_ca_needs_no_mailbox_and_locks_the_portal_source(host: Host) -> None:
    """ADR 0112: Caddy's own authority; ACME_EMAIL is not asked for."""
    settings = {k: v for k, v in PRODUCTION.items() if k != "ACME_EMAIL"}

    result = host.run("--mode", "test", "--internal-ca", "--non-interactive", **settings)

    assert result.returncode == 0, result.stderr
    assert host.settings("proxy.env") == {
        "TLS_DOMAIN": "afspraak.example.nl",
        "TLS_INTERNAL": "true",
        "IDP_UPSTREAM": IDP_UPSTREAM,
    }
    assert host.settings(".env.update")["COMPOSE_PROFILES"] == "idp,tls"
    assert host.settings(".env")["TLS_CERTIFICATE_SOURCE"] == "own"


def test_the_internal_ca_excludes_the_other_sources_and_the_plain_shape(host: Host) -> None:
    both = host.run(
        "--mode", "test", "--own-certificate", "--internal-ca", "--non-interactive", **PRODUCTION
    )
    plain = host.run("--mode", "test", "--no-tls", "--internal-ca", "--non-interactive")

    assert both.returncode == 1
    assert "exclude each other" in both.stderr
    assert plain.returncode == 1
    assert "--internal-ca needs the HTTPS proxy" in plain.stderr


def test_the_internal_ca_in_production_is_allowed_with_a_warning(host: Host) -> None:
    settings = {k: v for k, v in PRODUCTION.items() if k != "ACME_EMAIL"}

    result = host.run("--mode", "production", "--internal-ca", "--non-interactive", **settings)

    assert result.returncode == 0, result.stderr
    assert "not trust the certificate until its root is installed" in result.stderr


# The released proxy image (ADR 0064 step 2): pulled, verified and pinned like the application.


def test_an_unsigned_proxy_image_changes_nothing(host: Host) -> None:
    host.given("fail", "cosign-proxy\n")

    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 1
    assert f"{PROXY_RELEASE} is not signed by a release" in result.stderr
    assert not host.file(".env.update").exists()
    assert not host.ran(" up -d")


def test_a_rerun_pins_the_proxy_image_of_an_installation_from_before_it_was_released(
    host: Host,
) -> None:
    assert host.run("--mode", "production", "--non-interactive", **PRODUCTION).returncode == 0
    pin = host.file(".env.update")
    pin.write_text(pin.read_text().replace(f"OPENPLANNER_PROXY_IMAGE={PROXY_RELEASE}\n", ""))
    host.given("latest", "v1.5.0\n")
    (host.fakes / "calls.jsonl").unlink()

    result = host.run()

    assert result.returncode == 0, result.stderr
    # The version comes from the pinned application image, never from the latest release.
    assert host.settings(".env.update")["OPENPLANNER_IMAGE"] == RELEASE
    assert host.settings(".env.update")["OPENPLANNER_PROXY_IMAGE"] == PROXY_RELEASE
    assert not host.ran(f"pull -q {IMAGE}:")
    assert host.ran(f"pull -q {PROXY_IMAGE}:1.4.0")
    assert host.ran(f"cosign verify {PROXY_RELEASE}")


def test_a_new_version_pins_both_images_at_that_version(host: Host) -> None:
    assert host.run("--mode", "production", "--non-interactive", **PRODUCTION).returncode == 0

    result = host.run("--version", "1.5.0", "--non-interactive")

    assert result.returncode == 0, result.stderr
    assert host.settings(".env.update")["OPENPLANNER_IMAGE"] == digest(IMAGE, "1.5.0")
    assert host.settings(".env.update")["OPENPLANNER_PROXY_IMAGE"] == digest(PROXY_IMAGE, "1.5.0")


# Trust by installer mode (ADR 0067): the channel itself is chosen in the portal.


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


# A configuration package (ADR 0089 step 4): the login comes from the package, the rest after
# the web container is healthy.


CONFIG_ENV = "secrets/config.env"


def _package(host: Host) -> dict[str, str]:
    package = host.root / "openplanner-configuration-test.sqlite"
    package.write_bytes(b"SQLite format 3\0a package")
    package.chmod(0o600)
    passphrase = host.root / "passphrase"
    passphrase.write_text("a passphrase of sixteen chars\n")
    passphrase.chmod(0o600)
    return {"--configuration": str(package), "--passphrase-file": str(passphrase)}


def _with_package(host: Host, *args: str, **settings: str) -> subprocess.CompletedProcess[str]:
    options = [item for pair in _package(host).items() for item in pair]
    return host.run(*args, *options, "--non-interactive", **settings)


HOST_SETTINGS = {k: v for k, v in PRODUCTION.items() if not k.startswith("OIDC_")}


def test_the_configuration_and_passphrase_options_go_together(host: Host) -> None:
    options = _package(host)
    for option, path in options.items():
        result = host.run("--mode", "production", option, path, "--non-interactive", **PRODUCTION)

        assert result.returncode == 1
        assert "--configuration and --passphrase-file go together" in result.stderr
        assert not host.file(".env").exists()

    missing = host.run(
        "--mode",
        "production",
        "--configuration",
        options["--configuration"],
        "--passphrase-file",
        str(host.root / "nowhere"),
        "--non-interactive",
        **PRODUCTION,
    )
    assert missing.returncode == 1
    assert "cannot read the passphrase file" in missing.stderr


def test_the_package_answers_the_login_questions_and_is_imported_once_healthy(
    host: Host,
) -> None:
    result = _with_package(host, "--mode", "production", **HOST_SETTINGS)

    assert result.returncode == 0, result.stderr
    # The login values from the package, in their own file, quoted as the installer quotes.
    assert mode(host.file(CONFIG_ENV)) == 0o600
    assert host.settings(CONFIG_ENV) == {
        "OIDC_DISCOVERY_URL": "https://idp.example.nl/.well-known/openid-configuration",
        "OIDC_CLIENT_ID": "openplanner-admin",
        "OIDC_CLIENT_SECRET": "secret-from-the-package",
        "OIDC_SCOPES": "openid profile email",
    }
    env = host.settings(".env")
    assert not {key for key in env if key.startswith("OIDC_")}
    assert env["PUBLIC_BASE_URL"] == "https://afspraak.example.nl"
    commands = host.commands()
    # The package is read by a one-off container before .env exists; the passphrase never on
    # the command line, the package copy readable by the container's user and removed again.
    env_read = next(c for c in commands if "app.cli config env" in c)
    assert "run --rm --no-deps -T" in env_read
    assert "--passphrase-file /dev/stdin" in env_read
    assert "a passphrase of sixteen chars" not in " ".join(commands)
    assert not list(host.checkout.glob(".package.*"))
    # The import waits for health and uses a copy inside the container.
    started = commands.index(next(c for c in commands if c.endswith(" up -d")))
    imported = commands.index(next(c for c in commands if "app.cli config import" in c))
    assert started < imported
    assert any(c.endswith("web:/srv/data/configuration-package.sqlite") for c in commands)
    assert host.ran("exec -T web rm -f /srv/data/configuration-package.sqlite")
    assert "organisations       2" in result.stdout
    assert "secret-from-the-package" not in result.stdout + result.stderr


def test_a_refused_import_stops_the_installation_with_a_clear_error(host: Host) -> None:
    host.given("fail", "config-import\n")

    result = _with_package(host, "--mode", "production", **HOST_SETTINGS)

    assert result.returncode == 1
    assert "configuration_object_in_use" in result.stderr
    assert "converts to" in result.stderr
    assert host.ran("exec -T web rm -f /srv/data/configuration-package.sqlite")
    assert not host.ran("openplanner-updater.timer")


def test_a_package_without_a_login_leaves_the_questions_to_the_environment(host: Host) -> None:
    host.given("config-env", "")

    result = _with_package(host, "--mode", "production", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert host.settings(CONFIG_ENV) == {}
    assert host.settings(".env")["OIDC_CLIENT_SECRET"] == "client-secret-from-the-idp"


def test_a_wrong_passphrase_stops_before_anything_is_written(host: Host) -> None:
    host.given("fail", "config-env\n")

    result = _with_package(host, "--mode", "production", **HOST_SETTINGS)

    assert result.returncode == 1
    assert "configuration_passphrase_wrong" in result.stderr
    assert not host.file(CONFIG_ENV).exists()
    assert not host.file(".env").exists()
    assert not host.ran(" up -d")


def test_non_interactive_runs_take_the_package_from_the_environment(host: Host) -> None:
    options = _package(host)

    result = host.run(
        "--mode",
        "production",
        "--non-interactive",
        OPENPLANNER_CONFIGURATION=options["--configuration"],
        OPENPLANNER_PASSPHRASE_FILE=options["--passphrase-file"],
        **HOST_SETTINGS,
    )

    assert result.returncode == 0, result.stderr
    assert host.settings(CONFIG_ENV)["OIDC_CLIENT_SECRET"] == "secret-from-the-package"
    assert host.ran("app.cli config import")


def test_the_login_in_config_env_satisfies_the_production_checks(host: Host) -> None:
    # A rerun without the package: .env has no OIDC_*, config.env has them; nothing to report.
    assert _with_package(host, "--mode", "production", **HOST_SETTINGS).returncode == 0
    (host.fakes / "calls.jsonl").unlink()

    result = host.run("--non-interactive")

    assert result.returncode == 0, result.stderr
    assert not host.ran("app.cli config")
    assert host.settings(CONFIG_ENV)["OIDC_CLIENT_SECRET"] == "secret-from-the-package"

    # An explicit .env still wins: the check follows it and reports what is missing there.
    host.file(".env").write_text(
        host.file(".env").read_text() + "OIDC_DISCOVERY_URL='http://idp.example.nl/.well-known'\n"
    )
    again = host.run("--non-interactive")
    assert again.returncode == 1
    assert "OIDC_DISCOVERY_URL must start with https://" in again.stderr


# ADR 0106 step 5: the identity provider.


def test_a_test_install_renders_the_bundled_realm_and_hands_over_the_login(host: Host) -> None:
    settings = {k: v for k, v in PRODUCTION.items() if not k.startswith("OIDC_")}

    result = host.run("--mode", "test", "--non-interactive", **settings)

    assert result.returncode == 0, result.stderr
    env, app = host.settings(".env"), host.settings("secrets/keycloak-app.env")
    assert "OIDC_DISCOVERY_URL" not in env
    assert "OIDC_CLIENT_SECRET" not in env
    assert app["OIDC_ISSUER"] == "https://afspraak.example.nl/idp/realms/openplanner"
    assert app["KEYCLOAK_ADMIN_URL"] == f"http://{IDP_UPSTREAM}/idp/admin/realms/openplanner"
    assert host.settings("secrets/keycloak.env")["KC_HOSTNAME"] == "https://afspraak.example.nl/idp"
    assert host.file("secrets/keycloak-realm.json").is_file()
    assert "KC_SUPER_PASSWORD in" in result.stderr + result.stdout
    assert app["OIDC_CLIENT_SECRET"] not in result.stderr + result.stdout  # a path, never a value
    # A first install makes its secrets on the fixed address: nothing to replace (ADR 0125).
    assert "client secrets" not in result.stderr + result.stdout
    assert not host.file("secrets/keycloak-secrets.pending").exists()


def test_the_realms_mail_server_is_set_after_the_start_without_a_password_in_an_argument(
    host: Host,
) -> None:
    settings = {k: v for k, v in PRODUCTION.items() if not k.startswith("OIDC_")}

    result = host.run("--mode", "test", "--non-interactive", **settings)

    assert result.returncode == 0, result.stderr
    assert host.ran("exec -T idp sh -c")
    assert not host.ran("pass word #1")  # the password goes through stdin
    assert "mail server: smtp.example.nl" in result.stderr + result.stdout


def test_without_an_smtp_host_the_install_says_keycloak_cannot_mail(host: Host) -> None:
    settings = {k: v for k, v in PRODUCTION.items() if not k.startswith(("OIDC_", "SMTP_"))}

    result = host.run("--mode", "test", "--non-interactive", **settings)

    assert result.returncode == 0, result.stderr
    assert "Keycloak cannot mail invitations" in result.stderr
    assert not host.ran("exec -T idp")


def test_own_asks_for_the_login_as_before_and_renders_nothing(host: Host) -> None:
    result = host.run("--mode", "test", "--idp", "own", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    env = host.settings(".env")
    assert env["OIDC_DISCOVERY_URL"] == PRODUCTION["OIDC_DISCOVERY_URL"]
    assert not host.file("secrets/keycloak-realm.json").exists()
    assert "IDP_UPSTREAM" not in host.settings("proxy.env")
    pins = host.settings(".env.update")
    assert (pins["OPENPLANNER_IDP"], pins["COMPOSE_PROFILES"]) == ("own", "tls")


def test_a_lab_with_its_own_provider_keeps_mailpit_without_the_dev_keycloak(host: Host) -> None:
    result = host.run("--mode", "lab", "--idp", "own", "--version", "1.4.0", "--non-interactive")

    assert result.returncode == 0, result.stderr
    assert host.settings(".env.update")["COMPOSE_PROFILES"] == "mailpit"


def test_the_choice_is_remembered_and_a_rerun_keeps_the_rendered_secrets(host: Host) -> None:
    first = host.run("--mode", "lab", "--version", "1.4.0", "--non-interactive")
    secret = host.settings("secrets/keycloak.env")["KC_SUPER_PASSWORD"]

    again = host.run("--non-interactive")

    assert first.returncode == again.returncode == 0, again.stderr
    assert host.settings(".env.update")["OPENPLANNER_IDP"] == "bundled"
    assert host.settings("secrets/keycloak.env")["KC_SUPER_PASSWORD"] == secret


def test_a_changed_public_address_moves_the_realm_along(host: Host) -> None:
    settings = {k: v for k, v in PRODUCTION.items() if not k.startswith("OIDC_")}
    host.run("--mode", "test", "--non-interactive", **settings)

    env = host.file(".env")
    env.write_text(
        env.read_text().replace("https://afspraak.example.nl", "https://nieuw.example.nl")
    )

    moved = host.run("--mode", "test", "--non-interactive", **settings)

    assert moved.returncode == 0, moved.stderr
    assert host.settings("secrets/keycloak.env")["KC_HOSTNAME"] == "https://nieuw.example.nl/idp"


def _production_bundled(host: Host) -> subprocess.CompletedProcess[str]:
    settings = {k: v for k, v in PRODUCTION.items() if not k.startswith("OIDC_")}
    result = host.run("--mode", "production", "--non-interactive", **settings)
    assert result.returncode == 0, result.stderr
    return result


def test_production_runs_the_bundled_keycloak_on_its_own_database(host: Host) -> None:
    """ADR 0106 point 4: `start` on PostgreSQL, the second factor on, nothing asked."""
    _production_bundled(host)

    pins = host.settings(".env.update")
    keycloak = host.settings("secrets/keycloak.env")
    assert pins["OPENPLANNER_IDP"] == "bundled"
    assert pins["OPENPLANNER_IDP_START"] == "start"
    assert {"idp", "idp-db"} <= set(pins["COMPOSE_PROFILES"].split(","))
    assert keycloak["KC_DB"] == "postgres"
    assert keycloak["KC_DB_URL_HOST"] == IDP_DB_ADDRESS  # never the name (ADR 0125 step 2)
    assert keycloak["KC_HTTP_ENABLED"] == "true"
    assert host.settings("secrets/idp-db.env")["POSTGRES_PASSWORD"] == keycloak["KC_DB_PASSWORD"]
    assert host.settings(".env")["ADMIN_MFA_REQUIRED"] == "true"


class _ContainerSettings(Settings):
    """The settings from the values given, never from the environment of the test run."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read the arguments only: no variable, ``X_FILE`` or alias of the shell gets in."""
        return (init_settings,)


def _container_settings(host: Host) -> Settings:
    """Build the settings web and worker would start with on this host.

    The fake compose never runs the image, so the start-up check runs here: on the env files
    the containers read, in the order ``docker-compose.yml`` lists them (a later file wins).
    """
    values: dict[str, str] = {}
    for entry in compose_file()["x-app"]["env_file"]:
        if host.file(entry["path"]).is_file():
            values |= host.settings(entry["path"])
    return _ContainerSettings(**{key.lower(): value for key, value in values.items()})


def test_web_and_worker_start_on_what_a_production_install_with_the_bundled_keycloak_wrote(
    host: Host,
) -> None:
    """ADR 0125: the login is the bundled provider's internal hop, which production accepts."""
    _production_bundled(host)

    settings = _container_settings(host)

    assert settings.environment == "prod"
    assert settings.oidc_discovery_url == BUNDLED_IDP_DISCOVERY_URL
    assert settings.oidc_issuer == "https://afspraak.example.nl/idp/realms/openplanner"
    assert settings.production_problems() == []


def test_production_with_the_bundled_provider_no_longer_warns(host: Host) -> None:
    """ADR 0125 point 7 warned until step 2 gave every hop on the host a fixed address."""
    result = _production_bundled(host)

    assert "ADR 0125" not in result.stderr
    assert "--idp own" not in result.stderr


SECRET_KEYS = ("KC_OPENPLANNER_CLIENT_SECRET", "KC_DIRECTORY_CLIENT_SECRET")
MARK = "secrets/keycloak-secrets.pending"


def _secrets(host: Host) -> dict[str, str]:
    """Return the bundled provider's two client secrets as ``secrets/keycloak.env`` holds them."""
    kept = host.settings("secrets/keycloak.env")
    return {key: kept[key] for key in SECRET_KEYS}


def _as_before_adr_0125_step_2(host: Host) -> dict[str, str]:
    """Give an installed host the files it had before ADR 0125 step 2; return its secrets.

    The render script wrote the name ``idp`` into the login and the directory and ``idp-db`` as
    Keycloak's database, did not mark its client secrets, and the installer wrote
    ``IDP_UPSTREAM=idp:8080``. The fake's logs start again, so the next run is read on its own.
    """
    for name, pinned, by_name in (
        ("secrets/keycloak-app.env", IDP_UPSTREAM, "idp:8080"),
        ("proxy.env", f"IDP_UPSTREAM={IDP_UPSTREAM}", "IDP_UPSTREAM=idp:8080"),
        ("secrets/keycloak.env", f"KC_DB_URL_HOST={IDP_DB_ADDRESS}", "KC_DB_URL_HOST=idp-db"),
        ("secrets/keycloak.env", "KC_CLIENT_SECRETS_PINNED=true\n", ""),
    ):
        path = host.file(name)
        assert pinned in path.read_text()
        path.write_text(path.read_text().replace(pinned, by_name))
    for log in ("calls.jsonl", "idp-stdin.jsonl"):
        (host.fakes / log).unlink(missing_ok=True)
    return _secrets(host)


def _secrets_handed_to_keycloak(host: Host) -> list[list[str]]:
    """Return what each attempt to set the client secrets in Keycloak got on stdin."""
    log = host.fakes / "idp-stdin.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [call["stdin"].split() for call in calls if "get clients" in call["script"]]


def test_a_host_that_ran_on_the_names_moves_to_the_fixed_addresses_with_new_secrets(
    host: Host,
) -> None:
    """ADR 0125 step 2: whatever answered a name may hold both client secrets, so both go."""
    _production_bundled(host)
    old = _as_before_adr_0125_step_2(host)
    others = ("KC_SUPER_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD", "KC_DB_PASSWORD")
    unchanged = {key: host.settings("secrets/keycloak.env")[key] for key in others}

    result = host.run("--non-interactive")

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    keycloak, app = host.settings("secrets/keycloak.env"), host.settings("secrets/keycloak-app.env")
    # Every hop at its fixed address: the proxy's route, the login, the directory, the database.
    assert host.settings("proxy.env")["IDP_UPSTREAM"] == IDP_UPSTREAM
    assert "instead of idp:8080" in output
    assert app["OIDC_DISCOVERY_URL"] == BUNDLED_IDP_DISCOVERY_URL
    assert app["KEYCLOAK_ADMIN_URL"] == f"http://{IDP_UPSTREAM}/idp/admin/realms/openplanner"
    assert keycloak["KC_DB_URL_HOST"] == IDP_DB_ADDRESS
    # Both secrets new, in all three files; nothing else replaced.
    new = _secrets(host)
    assert all(new[key] != old[key] for key in SECRET_KEYS)
    assert [app["OIDC_CLIENT_SECRET"], app["KEYCLOAK_DIRECTORY_CLIENT_SECRET"]] == [*new.values()]
    realm = host.file("secrets/keycloak-realm.json").read_text()
    assert all(value in realm for value in new.values())
    assert not any(value in realm for value in old.values())
    assert {key: keycloak[key] for key in others} == unchanged
    # Keycloak takes them once it runs, after web and worker: through stdin, never an argument.
    assert _secrets_handed_to_keycloak(host) == [[*new.values()]]
    assert not any(host.ran(value) for value in (*new.values(), *old.values()))
    commands = host.commands()
    started = max(i for i, c in enumerate(commands) if c.endswith(" up -d"))
    ready = max(i for i, c in enumerate(commands) if "/readyz" in c)
    taken = max(i for i, c in enumerate(commands) if "exec -T idp" in c and "get clients" in c)
    assert started < ready < taken
    assert "took its new client secrets" in output
    assert not host.file(MARK).exists()
    assert _container_settings(host).production_problems() == []


def test_a_refused_secret_change_stops_the_run_and_the_next_run_sets_the_same_pair(
    host: Host,
) -> None:
    """Until Keycloak has the new secrets staff cannot log in: loud, and finished by a rerun."""
    _production_bundled(host)
    old = _as_before_adr_0125_step_2(host)
    host.given("fail", "idp-secrets\n")

    refused = host.run("--non-interactive", HEALTH_TIMEOUT="2")

    assert refused.returncode == 1
    assert "did not take its new client secrets" in refused.stderr
    assert "KC_BOOTSTRAP_ADMIN_PASSWORD" in refused.stderr
    assert host.file(MARK).exists()
    made = _secrets(host)
    assert all(made[key] != old[key] for key in SECRET_KEYS)

    host.given("fail", "")
    (host.fakes / "idp-stdin.jsonl").unlink()
    again = host.run("--non-interactive")

    assert again.returncode == 0, again.stderr
    assert _secrets(host) == made  # the same pair, not a third one
    assert _secrets_handed_to_keycloak(host) == [[*made.values()]]
    assert not host.file(MARK).exists()


def test_a_file_the_installer_cannot_read_is_never_emptied(host: Host) -> None:
    """Dropping lines replaces a file only when it was read: the secrets in it must survive."""
    _production_bundled(host)
    kept = host.file("secrets/keycloak.env")
    before = kept.read_text()
    _as_before_adr_0125_step_2(host)
    unpinned = kept.read_text()
    assert unpinned != before
    kept.chmod(0)
    try:
        result = host.run("--non-interactive")
    finally:
        kept.chmod(0o600)

    assert result.returncode == 1
    assert f"could not rewrite {kept}" in result.stderr
    assert kept.read_text() == unpinned  # not empty, not half written


def test_a_rerun_on_the_fixed_address_keeps_the_client_secrets(host: Host) -> None:
    _production_bundled(host)
    before = _secrets(host)
    (host.fakes / "idp-stdin.jsonl").unlink(missing_ok=True)

    again = host.run("--non-interactive")

    assert again.returncode == 0, again.stderr
    assert _secrets(host) == before
    assert _secrets_handed_to_keycloak(host) == []
    assert "client secrets" not in again.stdout + again.stderr


def _leave_and_return(host: Host) -> subprocess.CompletedProcess[str]:
    """Move an installed host to its own provider (--idp own) and back to the bundled one."""
    with host.file(".env").open("a") as env:
        env.writelines(
            f"{key}='{PRODUCTION[key]}'\n" for key in ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_SECRET")
        )
    assert host.run("--idp", "own", "--non-interactive").returncode == 0
    return host.run("--idp", "bundled", "--non-interactive")


def test_a_host_back_from_its_own_provider_keeps_a_pair_made_on_the_fixed_address(
    host: Host,
) -> None:
    """The render script marked the pair: it never travelled to the name idp."""
    _production_bundled(host)
    before = _secrets(host)

    back = _leave_and_return(host)

    assert back.returncode == 0, back.stderr
    assert _secrets(host) == before
    assert host.settings("secrets/keycloak.env")["KC_CLIENT_SECRETS_PINNED"] == "true"
    assert _secrets_handed_to_keycloak(host) == []


def test_a_host_that_left_before_step_2_gets_a_new_pair_on_its_way_back(host: Host) -> None:
    """Its unmarked pair waited in secrets/keycloak.env, and the realm in the volume has it too."""
    _production_bundled(host)
    old = _as_before_adr_0125_step_2(host)

    back = _leave_and_return(host)

    assert back.returncode == 0, back.stderr
    new = _secrets(host)
    assert all(new[key] != old[key] for key in SECRET_KEYS)
    assert _secrets_handed_to_keycloak(host)[-1] == [*new.values()]
    assert not host.file(MARK).exists()


def test_a_render_by_hand_before_the_installer_does_not_keep_the_old_pair(host: Host) -> None:
    """System status and the runbook send operators to the render script; it keeps values."""
    _production_bundled(host)
    old = _as_before_adr_0125_step_2(host)
    render = subprocess.run(  # noqa: S603 - the checkout's own script
        [BASH, str(host.file("scripts/render-keycloak-realm.sh")), "--dir", str(host.checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert render.returncode == 0, render.stderr
    assert (
        host.settings("secrets/keycloak-app.env")["OIDC_DISCOVERY_URL"] == BUNDLED_IDP_DISCOVERY_URL
    )
    assert _secrets(host) == old  # the login moved, the pair did not

    result = host.run("--non-interactive")

    assert result.returncode == 0, result.stderr
    new = _secrets(host)
    assert all(new[key] != old[key] for key in SECRET_KEYS)
    assert _secrets_handed_to_keycloak(host) == [[*new.values()]]


def test_a_host_that_leaves_the_bundled_keycloak_logs_in_with_its_own_provider(
    host: Host,
) -> None:
    """ADR 0125 point 6: the bundled login, route and containers go; the volumes stay."""
    _production_bundled(host)
    own = {key: PRODUCTION[key] for key in ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_SECRET")}
    with host.file(".env").open("a") as env:
        env.writelines(f"{key}='{value}'\n" for key, value in own.items())

    result = host.run("--idp", "own", "--non-interactive")

    assert result.returncode == 0, result.stderr
    assert not host.file("secrets/keycloak-app.env").exists()
    assert host.file("secrets/keycloak.env").is_file()  # the way back to --idp bundled
    assert "removed secrets/keycloak-app.env" in result.stderr + result.stdout
    assert "IDP_UPSTREAM" not in host.settings("proxy.env")  # no /idp route to the name idp
    # Only once the new proxy, web and worker run without it (`up -d`, then ready): stopped
    # earlier, the name idp would reach the host's resolver while they still use it. `compose rm`
    # never removes the named volumes idp-data and idp-db-data, which the fake docker cannot show.
    commands = host.commands()
    retired = max(i for i, c in enumerate(commands) if "rm --stop --force idp idp-db" in c)
    started = max(i for i, c in enumerate(commands) if c.endswith(" up -d"))
    ready = max(i for i, c in enumerate(commands) if "/readyz" in c)
    assert started < ready < retired
    assert "containers (idp, idp-db) are removed" in result.stderr + result.stdout
    settings = _container_settings(host)
    assert settings.oidc_discovery_url == own["OIDC_DISCOVERY_URL"]
    assert settings.keycloak_admin_url is None
    assert settings.production_problems() == []


def test_test_and_lab_keep_the_development_start_and_h2(host: Host) -> None:
    result = host.run("--mode", "test", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert "OPENPLANNER_IDP_START" not in host.settings(".env.update")
    assert "idp-db" not in host.settings(".env.update")["COMPOSE_PROFILES"]
    assert "KC_DB" not in host.settings("secrets/keycloak.env")


def test_a_production_host_with_its_own_login_keeps_it(host: Host) -> None:
    result = host.run("--mode", "production", "--non-interactive", **PRODUCTION)

    assert result.returncode == 0, result.stderr
    assert host.settings(".env.update")["OPENPLANNER_IDP"] == "own"


def test_an_unknown_provider_choice_is_refused(host: Host) -> None:
    result = host.run("--mode", "test", "--idp", "ldap", "--non-interactive", **PRODUCTION)

    assert result.returncode == 1
    assert "--idp must be bundled or own" in result.stderr


# A configuration document (ADR 0099): planned, then applied with the hash of that plan.

DOCUMENT_IN_WEB = "/srv/data/configuration-document.json"
SEALED = '{"format": "openplanner.config/v1", "x": {"sealed": "abc"}}'


def _document(host: Host, text: str = '{"format": "openplanner.config/v1"}') -> Path:
    document = host.root / "configuration.json"
    document.write_text(text)
    document.chmod(0o600)
    return document


def _passphrase_file(host: Host) -> Path:
    passphrase = host.root / "passphrase"
    passphrase.write_text("a passphrase of sixteen chars\n")
    passphrase.chmod(0o600)
    return passphrase


def _install_document(host: Host, text: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return host.run(
        "--mode",
        "production",
        "--configuration",
        str(_document(host, text)),
        *extra,
        "--non-interactive",
        **PRODUCTION,
    )


def test_a_document_is_planned_and_applied_with_the_hash_of_its_plan(host: Host) -> None:
    result = _install_document(host, '{"format": "openplanner.config/v1"}')

    assert result.returncode == 0, result.stderr
    commands = host.commands()
    started = commands.index(next(c for c in commands if c.endswith(" up -d")))
    planned = commands.index(next(c for c in commands if "app.cli config plan" in c))
    applied = commands.index(next(c for c in commands if "app.cli config apply" in c))
    assert started < planned < applied
    assert any(c.endswith(f"web:{DOCUMENT_IN_WEB}") for c in commands)
    assert host.ran(f"config apply {DOCUMENT_IN_WEB} --plan-hash {'ab' * 32}")
    assert host.ran(f"exec -T web rm -f {DOCUMENT_IN_WEB}")
    # No passphrase was given or needed; no login file is written, the login is asked as usual.
    assert not host.ran("--passphrase-file")
    assert not host.ran("app.cli config env")
    assert not host.file(CONFIG_ENV).exists()
    assert host.settings(".env")["OIDC_CLIENT_SECRET"] == "client-secret-from-the-idp"
    assert "create group acme/team: name" in result.stdout
    assert "applied; settings version" in result.stdout


def test_a_document_with_sealed_secrets_takes_its_passphrase_on_standard_input(
    host: Host,
) -> None:
    passphrase = _passphrase_file(host)

    result = _install_document(host, SEALED, "--passphrase-file", str(passphrase))

    assert result.returncode == 0, result.stderr
    assert host.ran("config plan")
    apply = next(c for c in host.commands() if "app.cli config apply" in c)
    assert "--passphrase-file /dev/stdin" in apply
    assert "a passphrase of sixteen chars" not in " ".join(host.commands())


def test_a_document_with_sealed_secrets_needs_a_passphrase_file(host: Host) -> None:
    result = _install_document(host, SEALED)

    assert result.returncode == 1
    assert "sealed secrets: give --passphrase-file" in result.stderr
    assert not host.file(".env").exists()


def test_a_plan_with_a_refusal_applies_nothing_and_shows_the_report(host: Host) -> None:
    host.given("fail", "config-plan\n")

    result = _install_document(host, '{"format": "openplanner.config/v1"}')

    assert result.returncode == 1
    assert "REFUSED organisations[acme].groups[team] not_permitted name" in result.stderr
    assert "nothing was applied" in result.stderr
    assert not host.ran("app.cli config apply")
    assert host.ran(f"exec -T web rm -f {DOCUMENT_IN_WEB}")
    assert not host.ran("openplanner-updater.timer")


def test_a_plan_that_moved_on_is_refused_and_the_copy_removed(host: Host) -> None:
    host.given("fail", "config-apply\n")

    result = _install_document(host, '{"format": "openplanner.config/v1"}')

    assert result.returncode == 1
    assert "configuration_plan_stale" in result.stderr
    assert host.ran(f"exec -T web rm -f {DOCUMENT_IN_WEB}")


def test_a_wrong_passphrase_for_a_document_is_named(host: Host) -> None:
    host.given("fail", "config-passphrase\n")

    result = _install_document(host, SEALED, "--passphrase-file", str(_passphrase_file(host)))

    assert result.returncode == 1
    assert "wrong passphrase" in result.stderr
    assert not host.ran("app.cli config apply")


def test_a_file_that_is_neither_a_document_nor_a_package_is_refused_early(host: Host) -> None:
    result = _install_document(host, "not a configuration\n")

    assert result.returncode == 1
    assert "neither a configuration document" in result.stderr
    assert not host.file(".env").exists()


def test_a_passphrase_file_alone_is_refused(host: Host) -> None:
    alone = host.run(
        "--mode",
        "production",
        "--passphrase-file",
        str(_passphrase_file(host)),
        "--non-interactive",
        **PRODUCTION,
    )

    assert alone.returncode == 1
    assert "--configuration and --passphrase-file go together" in alone.stderr
