"""Which directory settings the bundled provider lacks (ADR 0106 point 3, ``app.main``)."""

from pydantic import SecretStr

from app.config import Settings
from app.main import _directory_missing

URL = "http://idp:8080/admin/realms/openplanner"
BUNDLED = "http://idp:8080/realms/openplanner/.well-known/openid-configuration"


def test_another_provider_lacks_nothing(settings: Settings) -> None:
    other = settings.model_copy(
        update={"oidc_discovery_url": "https://login.example.nl/.well-known/openid-configuration"}
    )

    assert _directory_missing(other) == ()


def test_the_bundled_realm_without_its_directory_lacks_both(settings: Settings) -> None:
    bundled = settings.model_copy(update={"oidc_discovery_url": BUNDLED})

    assert _directory_missing(bundled) == (
        "KEYCLOAK_ADMIN_URL",
        "KEYCLOAK_DIRECTORY_CLIENT_SECRET",
    )


def test_one_setting_alone_names_the_other(settings: Settings) -> None:
    url_only = settings.model_copy(update={"keycloak_admin_url": URL})
    secret_only = settings.model_copy(
        update={"keycloak_directory_client_secret": SecretStr("s" * 32)}
    )

    assert _directory_missing(url_only) == ("KEYCLOAK_DIRECTORY_CLIENT_SECRET",)
    assert _directory_missing(secret_only) == ("KEYCLOAK_ADMIN_URL",)


def test_a_complete_directory_lacks_nothing(settings: Settings) -> None:
    complete = settings.model_copy(
        update={
            "oidc_discovery_url": BUNDLED,
            "keycloak_admin_url": URL,
            "keycloak_directory_client_secret": SecretStr("s" * 32),
        }
    )

    assert _directory_missing(complete) == ()
