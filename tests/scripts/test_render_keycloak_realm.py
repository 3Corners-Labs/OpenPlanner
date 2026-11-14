"""The bundled identity provider (ADR 0090 step 1): the realm template and its renderer.

``scripts/render-keycloak-realm.sh`` generates the secrets of ``secrets/keycloak.env`` and
renders ``deploy/keycloak/realm.template.json`` into ``secrets/keycloak-realm.json``. Both
env files must be mode 600 and the realm 644 inside a 700 directory (Keycloak reads it as
its own user), the script prints no value, and nothing ``dev-only-`` may remain.
"""

import json
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.web_transport import BUNDLED_IDP_ADDRESS, BUNDLED_IDP_DISCOVERY_URL

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "scripts/render-keycloak-realm.sh"
TEMPLATE = REPO / "deploy/keycloak/realm.template.json"
DEV_REALM = REPO / "dev/keycloak/openplanner-realm.json"
BASH = shutil.which("bash") or "/bin/bash"
INTERNAL = f"http://{BUNDLED_IDP_ADDRESS}:8080"
"""Where web and worker reach the provider: its fixed address, never a name (ADR 0125)."""
FIXED = {
    "KC_BOOTSTRAP_ADMIN_PASSWORD": "fixed-admin-password-0123456789",
    "KC_OPENPLANNER_CLIENT_SECRET": "fixed-client-secret-0123456789",
    "KC_DIRECTORY_CLIENT_SECRET": "fixed-directory-secret-0123456789",
    "KC_SUPER_PASSWORD": "fixed-super-password-0123456789",
}


def render(
    directory: Path, env: str | None = None, *options: str
) -> subprocess.CompletedProcess[str]:
    """Run the renderer against ``directory`` as the checkout, with an optional env file."""
    target = directory / "deploy/keycloak"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE, target / TEMPLATE.name)
    secrets = directory / "secrets"
    secrets.mkdir(exist_ok=True)
    if env is not None:
        (secrets / "keycloak.env").write_text(env)
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [BASH, str(SCRIPT), "--dir", str(directory), *options],
        capture_output=True,
        text=True,
        check=False,
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


def test_the_template_has_the_clients_super_and_the_directory_service_account() -> None:
    template = json.loads(TEMPLATE.read_text())

    assert [client["clientId"] for client in template["clients"]] == [
        "openplanner-admin",
        "openplanner-directory",
    ]
    assert [user["username"] for user in template["users"]] == [
        "super",
        "service-account-openplanner-directory",
    ]


def test_the_directory_service_account_may_only_manage_users() -> None:
    """ADR 0095 point 1: ``manage-users`` and ``view-realm``, nothing else, no browser login."""
    template = json.loads(TEMPLATE.read_text())
    client = next(c for c in template["clients"] if c["clientId"] == "openplanner-directory")
    account = next(u for u in template["users"] if u["username"].startswith("service-account"))

    assert client["serviceAccountsEnabled"] is True
    assert not client["standardFlowEnabled"]
    assert not client["directAccessGrantsEnabled"]
    assert account["clientRoles"] == {"realm-management": ["manage-users", "view-realm"]}
    assert "realmRoles" not in account


def test_the_development_realm_has_the_same_directory_service_account() -> None:
    """ADR 0106 point 2: staff management works under ``--profile dev`` too."""
    dev = json.loads(DEV_REALM.read_text())
    template = json.loads(TEMPLATE.read_text())

    def directory(realm: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        client = next(c for c in realm["clients"] if c["clientId"] == "openplanner-directory")
        account = next(
            u for u in realm["users"] if u.get("serviceAccountClientId") == client["clientId"]
        )
        return {k: v for k, v in client.items() if k != "secret"}, account

    assert directory(dev) == directory(template)
    dev_client = next(c for c in dev["clients"] if c["clientId"] == "openplanner-directory")
    assert dev_client["secret"].startswith("dev-only-")


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
    assert mode(tmp_path / "secrets/keycloak-realm.json") == 0o644
    assert mode(tmp_path / "secrets") == 0o700
    for value in (env[key] for key in FIXED):
        assert value not in result.stdout + result.stderr
    assert (
        env["KC_OPENPLANNER_CLIENT_SECRET"]
        in (tmp_path / "secrets/keycloak-realm.json").read_text()
    )


def test_the_application_half_carries_the_realm_s_directory_secret(tmp_path: Path) -> None:
    """ADR 0106 point 2: web and worker get the directory without a value copied by hand."""
    env = "".join(f"{key}={value}\n" for key, value in FIXED.items())
    result = render(tmp_path, env)
    assert result.returncode == 0, result.stderr

    app = read_env(tmp_path / "secrets/keycloak-app.env")
    realm = json.loads((tmp_path / "secrets/keycloak-realm.json").read_text())
    client = next(c for c in realm["clients"] if c["clientId"] == "openplanner-directory")
    assert {k: v for k, v in app.items() if k.startswith("KEYCLOAK_")} == {
        "KEYCLOAK_ADMIN_URL": f"{INTERNAL}/admin/realms/openplanner",
        "KEYCLOAK_DIRECTORY_CLIENT_ID": "openplanner-directory",
        "KEYCLOAK_DIRECTORY_CLIENT_SECRET": FIXED["KC_DIRECTORY_CLIENT_SECRET"],
    }
    assert client["secret"] == app["KEYCLOAK_DIRECTORY_CLIENT_SECRET"]
    assert mode(tmp_path / "secrets/keycloak-app.env") == 0o600
    assert FIXED["KC_DIRECTORY_CLIENT_SECRET"] not in result.stdout + result.stderr


def test_web_and_worker_read_the_application_half_after_env() -> None:
    """The rendered directory values win over ``.env``: they must equal the realm's."""
    text = (REPO / "docker-compose.yml").read_text()
    block = text[text.index("x-app: &app") : text.index("\nservices:")]

    assert block.index("- path: .env") < block.index("- path: secrets/keycloak-app.env")


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
    """ADR 0100: acr ``mfa`` is level 2, and the flow asks for OTP at that level.

    OTP is required, not an alternative: Keycloak skips an alternative the person has not set
    up yet, so a first login (``CONFIGURE_TOTP`` pending) failed on docker01 with alternatives.
    WebAuthn stays in the flow, switched off, until it gets a condition of its own.
    """
    realm = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    (client,) = [c for c in realm["clients"] if c["clientId"] == "openplanner-admin"]
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
    requirement = {e["authenticator"]: e["requirement"] for e in second}
    assert requirement["auth-otp-form"] == "REQUIRED"
    assert requirement["webauthn-authenticator"] == "DISABLED"
    assert config["config"]["loa-condition-level"] == "2"
    assert "CONFIGURE_TOTP" in super_user["requiredActions"]
    assert realm["resetPasswordAllowed"] is True  # self-service reset at the login prompt


def test_the_otp_policy_is_the_one_every_authenticator_app_reads() -> None:
    """TOTP with SHA1, 6 digits and 30 seconds (RFC 6238 defaults).

    Google and Microsoft Authenticator, Duo Mobile and FreeOTP all read it. An import that
    names only the type sets the digits and the period to 0: the codes never match, and
    Keycloak offers FreeOTP alone (docker01, 2026-11-02).
    """
    realm = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    assert {k: v for k, v in realm.items() if k.startswith("otpPolicy")} == {
        "otpPolicyType": "totp",
        "otpPolicyAlgorithm": "HmacSHA1",
        "otpPolicyDigits": 6,
        "otpPolicyPeriod": 30,
        "otpPolicyLookAheadWindow": 1,
        "otpPolicyInitialCounter": 0,
    }


def test_an_https_address_puts_keycloak_under_idp_and_writes_the_login(tmp_path: Path) -> None:
    """ADR 0112: one host name, the proxy serves /idp; nothing is copied into .env by hand."""
    (tmp_path / ".env").write_text("PUBLIC_BASE_URL=https://docker01.example.nl\n")
    env = "".join(f"{key}={value}\n" for key, value in FIXED.items())

    result = render(tmp_path, env)

    assert result.returncode == 0, result.stderr
    keycloak = read_env(tmp_path / "secrets/keycloak.env")
    app = read_env(tmp_path / "secrets/keycloak-app.env")
    assert keycloak["KC_HOSTNAME"] == "https://docker01.example.nl/idp"
    assert keycloak["KC_HTTP_RELATIVE_PATH"] == "/idp"
    assert keycloak["KC_PROXY_HEADERS"] == "xforwarded"
    assert app["KEYCLOAK_ADMIN_URL"] == f"{INTERNAL}/idp/admin/realms/openplanner"
    # The one internal address production accepts over http (ADR 0125), with an https issuer.
    assert app["OIDC_DISCOVERY_URL"] == BUNDLED_IDP_DISCOVERY_URL
    assert app["OIDC_ISSUER"] == "https://docker01.example.nl/idp/realms/openplanner"
    assert app["OIDC_CLIENT_SECRET"] == FIXED["KC_OPENPLANNER_CLIENT_SECRET"]
    realm = json.loads((tmp_path / "secrets/keycloak-realm.json").read_text())
    client = next(c for c in realm["clients"] if c["clientId"] == "openplanner-admin")
    assert client["redirectUris"] == ["https://docker01.example.nl/admin/callback"]
    assert mode(tmp_path / "secrets/keycloak-app.env") == 0o600


def test_an_earlier_http_hostname_is_replaced_when_the_address_becomes_https(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("PUBLIC_BASE_URL=https://docker01.example.nl\n")
    env = "KC_HOSTNAME=http://docker01.example.nl:8780\n" + "".join(
        f"{key}={value}\n" for key, value in FIXED.items()
    )

    assert render(tmp_path, env).returncode == 0

    keycloak = read_env(tmp_path / "secrets/keycloak.env")
    assert keycloak["KC_HOSTNAME"] == "https://docker01.example.nl/idp"


def test_an_http_address_keeps_the_loopback_shape(tmp_path: Path) -> None:
    assert render(tmp_path).returncode == 0

    keycloak = read_env(tmp_path / "secrets/keycloak.env")
    app = read_env(tmp_path / "secrets/keycloak-app.env")
    assert "KC_HTTP_RELATIVE_PATH" not in keycloak
    assert app["OIDC_DISCOVERY_URL"] == (
        f"{INTERNAL}/realms/openplanner/.well-known/openid-configuration"
    )
    assert app["OIDC_ISSUER"] == "http://127.0.0.1:8080/realms/openplanner"
    assert app["OIDC_CLIENT_ID"] == "openplanner-admin"


def test_a_value_the_installer_quoted_is_read_without_its_quotes(tmp_path: Path) -> None:
    """install.sh writes KEY='value'; the address must not carry the quotes into the realm."""
    (tmp_path / ".env").write_text("PUBLIC_BASE_URL='https://docker01.example.nl'\n")

    result = render(tmp_path)

    assert result.returncode == 0, result.stderr
    assert read_env(tmp_path / "secrets/keycloak.env")["KC_HOSTNAME"] == (
        "https://docker01.example.nl/idp"
    )


def test_postgres_gives_keycloak_its_own_database_and_a_private_password(tmp_path: Path) -> None:
    """ADR 0106 point 4: ``start`` needs a database and plain http behind the proxy."""
    result = render(tmp_path, None, "--database", "postgres")

    assert result.returncode == 0, result.stderr
    keycloak = read_env(tmp_path / "secrets/keycloak.env")
    database = read_env(tmp_path / "secrets/idp-db.env")
    assert keycloak["KC_DB"] == "postgres"
    # The database's fixed address on its internal network, never the name idp-db (ADR 0125).
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    pinned = compose["services"]["idp-db"]["networks"]["idp-store"]["ipv4_address"]
    assert keycloak["KC_DB_URL_HOST"] == pinned
    assert keycloak["KC_HTTP_ENABLED"] == "true"
    assert keycloak["KC_PROXY_HEADERS"] == "xforwarded"
    assert database["POSTGRES_PASSWORD"] == keycloak["KC_DB_PASSWORD"] != ""
    assert mode(tmp_path / "secrets/idp-db.env") == 0o600
    assert keycloak["KC_DB_PASSWORD"] not in result.stderr


def test_the_database_choice_and_its_password_are_remembered(tmp_path: Path) -> None:
    render(tmp_path, None, "--database", "postgres")
    first = read_env(tmp_path / "secrets/keycloak.env")["KC_DB_PASSWORD"]

    again = render(tmp_path)

    assert again.returncode == 0, again.stderr
    assert read_env(tmp_path / "secrets/keycloak.env")["KC_DB_PASSWORD"] == first


def test_h2_writes_no_database_file(tmp_path: Path) -> None:
    render(tmp_path)

    assert not (tmp_path / "secrets/idp-db.env").exists()
    assert "KC_DB" not in read_env(tmp_path / "secrets/keycloak.env")


def test_an_unknown_database_is_refused(tmp_path: Path) -> None:
    assert render(tmp_path, None, "--database", "mysql").returncode == 2


def test_the_idp_db_service_is_private_and_waited_for() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            docker,
            "compose",
            "--profile",
            "idp",
            "--profile",
            "idp-db",
            "config",
            "--format",
            "json",
        ],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("docker compose plugin is not installed")
    compose = json.loads(result.stdout)
    db = compose["services"]["idp-db"]

    assert db["image"].startswith("postgres:")
    assert "ports" not in db
    assert compose["services"]["idp"]["depends_on"]["idp-db"]["condition"] == "service_healthy"
    assert "idp-db-data" in compose["volumes"]
