"""The bundled identity provider (ADR 0090 step 1): the realm template and its renderer.

``scripts/render-keycloak-realm.sh`` generates the secrets of ``secrets/keycloak.env`` and
renders ``deploy/keycloak/realm.template.json`` into ``secrets/keycloak-realm.json``. Both
files must be mode 600, the script prints no value, and nothing ``dev-only-`` may remain.
"""

import json
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "scripts/render-keycloak-realm.sh"
TEMPLATE = REPO / "deploy/keycloak/realm.template.json"
DEV_REALM = REPO / "dev/keycloak/openplanner-realm.json"
BASH = shutil.which("bash") or "/bin/bash"
FIXED = {
    "KC_BOOTSTRAP_ADMIN_PASSWORD": "fixed-admin-password-0123456789",
    "KC_OPENPLANNER_CLIENT_SECRET": "fixed-client-secret-0123456789",
    "KC_SUPER_PASSWORD": "fixed-super-password-0123456789",
}


def render(directory: Path, env: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the renderer against ``directory`` as the checkout, with an optional env file."""
    target = directory / "deploy/keycloak"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE, target / TEMPLATE.name)
    secrets = directory / "secrets"
    secrets.mkdir(exist_ok=True)
    if env is not None:
        (secrets / "keycloak.env").write_text(env)
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [BASH, str(SCRIPT), "--dir", str(directory)], capture_output=True, text=True, check=False
    )


def read_env(path: Path) -> dict[str, str]:
    """The ``KEY=value`` pairs of an env file."""
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    return {key: value for key, value in pairs}


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def rendered(tmp_path: Path) -> dict[str, Any]:
    """The realm rendered with the fixed values."""
    env = "".join(f"{key}={value}\n" for key, value in FIXED.items())
    result = render(tmp_path, env)
    assert result.returncode == 0, result.stderr
    realm: dict[str, Any] = json.loads((tmp_path / "secrets/keycloak-realm.json").read_text())
    return realm


def test_the_template_holds_no_development_secret() -> None:
    assert "dev-only-" not in TEMPLATE.read_text()


def test_the_template_has_only_the_client_and_super() -> None:
    template = json.loads(TEMPLATE.read_text())

    assert [client["clientId"] for client in template["clients"]] == ["openplanner-admin"]
    assert [user["username"] for user in template["users"]] == ["super"]


def test_the_template_keeps_the_development_realm_contract() -> None:
    """Same client mappers and the same roles for ``super`` as development, so a login that
    works against ``dev`` works against the bundled provider."""
    dev = json.loads(DEV_REALM.read_text())
    template = json.loads(TEMPLATE.read_text())
    dev_client = next(c for c in dev["clients"] if c["clientId"] == "openplanner-admin")
    dev_super = next(u for u in dev["users"] if u["username"] == "super")

    assert template["clients"][0]["protocolMappers"] == dev_client["protocolMappers"]
    assert template["users"][0]["attributes"] == dev_super["attributes"]
    assert template["realm"] == dev["realm"] == "openplanner"


def test_rendering_substitutes_the_fixed_values(rendered: dict[str, Any]) -> None:
    text = json.dumps(rendered)

    assert "dev-only-" not in text
    assert "${KC_" not in text, "every placeholder of ours is rendered"
    assert rendered["clients"][0]["secret"] == FIXED["KC_OPENPLANNER_CLIENT_SECRET"]
    assert rendered["users"][0]["credentials"][0]["value"] == FIXED["KC_SUPER_PASSWORD"]


def test_super_must_change_the_password_at_first_login(rendered: dict[str, Any]) -> None:
    super_user = rendered["users"][0]

    assert super_user["username"] == "super"
    assert super_user["requiredActions"] == ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]
    assert super_user["credentials"][0]["temporary"] is True


def test_super_holds_every_built_in_role(rendered: dict[str, Any]) -> None:
    attributes = rendered["users"][0]["attributes"]

    assert attributes["entitlements"] == ["openplanner:superuser"]
    assert set(attributes["roles"]) == {
        "openplanner-superuser",
        "openplanner-org-admin",
        "openplanner-group-admin",
        "openplanner-auditor",
        "openplanner-agent",
    }


def test_missing_values_are_generated_and_kept_private(tmp_path: Path) -> None:
    result = render(tmp_path)

    assert result.returncode == 0, result.stderr
    env = read_env(tmp_path / "secrets/keycloak.env")
    assert set(FIXED) <= set(env)
    assert all(len(env[key]) >= 32 for key in FIXED)
    assert mode(tmp_path / "secrets/keycloak.env") == 0o600
    assert mode(tmp_path / "secrets/keycloak-realm.json") == 0o600
    for value in (env[key] for key in FIXED):
        assert value not in result.stdout + result.stderr
    assert (
        env["KC_OPENPLANNER_CLIENT_SECRET"]
        in (tmp_path / "secrets/keycloak-realm.json").read_text()
    )


def test_existing_values_are_kept_on_a_second_run(tmp_path: Path) -> None:
    env = f"KC_SUPER_PASSWORD={FIXED['KC_SUPER_PASSWORD']}\n"
    assert render(tmp_path, env).returncode == 0
    first = read_env(tmp_path / "secrets/keycloak.env")

    assert render(tmp_path).returncode == 0

    assert read_env(tmp_path / "secrets/keycloak.env") == first
    assert first["KC_SUPER_PASSWORD"] == FIXED["KC_SUPER_PASSWORD"]


def test_the_public_address_sets_the_redirect_uris(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PUBLIC_BASE_URL=https://afspraak.example.nl\n")

    assert render(tmp_path).returncode == 0

    client = json.loads((tmp_path / "secrets/keycloak-realm.json").read_text())["clients"][0]
    assert client["redirectUris"] == ["https://afspraak.example.nl/admin/callback"]
    assert client["attributes"]["post.logout.redirect.uris"] == (
        "https://afspraak.example.nl/admin/logged-out"
    )


def test_a_value_the_template_cannot_carry_is_refused(tmp_path: Path) -> None:
    result = render(tmp_path, 'KC_SUPER_PASSWORD=with"quote\n')

    assert result.returncode != 0
    assert "KC_SUPER_PASSWORD" in result.stderr
    assert 'with"quote' not in result.stderr
    assert not (tmp_path / "secrets/keycloak-realm.json").exists()


def _compose_config() -> dict[str, Any]:
    """The resolved compose file with the ``idp`` profile, or a skip without docker."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["docker", "compose", "--profile", "idp", "config", "--format", "json"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if "not a docker command" in result.stderr:
        pytest.skip("docker compose plugin is not installed")
    assert result.returncode == 0, result.stderr
    config: dict[str, Any] = json.loads(result.stdout)
    return config


def _service_text(name: str) -> str:
    """The lines of one service in ``docker-compose.yml`` as written (not resolved)."""
    text = (REPO / "docker-compose.yml").read_text()
    start = text.index(f"\n  {name}:\n")
    end = text.find("\n  ", start + 1)
    while end != -1 and text[end + 3 : end + 4] in (" ", "#", "\n"):
        end = text.find("\n  ", end + 1)
    return text[start : end if end != -1 else len(text)]


def test_the_compose_profile_idp_mounts_the_rendered_realm() -> None:
    compose = _compose_config()
    service = compose["services"]["idp"]

    assert service["profiles"] == ["idp"]
    assert service["image"] == "quay.io/keycloak/keycloak:26.8.1"
    assert "keycloak" not in compose["services"], "the dev profile is not active under idp"
    assert "KC_BOOTSTRAP_ADMIN_PASSWORD:" not in _service_text("idp"), (
        "the password comes from the file"
    )
    assert all(port["host_ip"] == "127.0.0.1" for port in service["ports"])
    mounts = {volume["source"]: volume["target"] for volume in service["volumes"]}
    assert mounts[str(REPO / "secrets/keycloak-realm.json")] == (
        "/opt/keycloak/data/import/openplanner-realm.json"
    )
    assert mounts["idp-data"] == "/opt/keycloak/data"
    assert "idp-data" in compose["volumes"]


def test_the_dev_and_idp_services_pin_the_same_image() -> None:
    text = (REPO / "docker-compose.yml").read_text()

    assert len({line.strip() for line in text.splitlines() if "quay.io/keycloak" in line}) == 1


def test_the_realm_asks_for_a_second_factor_and_says_so_in_the_token() -> None:
    """ADR 0100: acr ``mfa`` is level 2, the flow asks for OTP or WebAuthn at that level."""
    realm = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    (client,) = realm["clients"]
    flows = {f["alias"]: f for f in realm["authenticationFlows"]}
    second = flows["openplanner second factor"]["authenticationExecutions"]
    (config,) = realm["authenticatorConfig"]
    (super_user,) = [u for u in realm["users"] if u["username"] == "super"]

    assert json.loads(client["attributes"]["acr.loa.map"]) == {"mfa": 2}
    assert client["attributes"]["default.acr.values"] == "mfa"
    assert realm["browserFlow"] == "openplanner browser"
    assert {e["authenticator"] for e in second} == {
        "conditional-level-of-authentication",
        "auth-otp-form",
        "webauthn-authenticator",
    }
    assert config["config"]["loa-condition-level"] == "2"
    assert "CONFIGURE_TOTP" in super_user["requiredActions"]
    assert realm["resetPasswordAllowed"] is True  # self-service reset at the login prompt
