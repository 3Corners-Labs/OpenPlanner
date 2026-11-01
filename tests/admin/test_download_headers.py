"""Every download names its file safely (ASVS V5.4.1, V12.3.2).

A ``content-disposition`` filename is lower-case letters, digits, dots and hyphens only: no
quote, path separator, control character or non-ASCII letter can end up in a header. The five
places that set the header are pinned, so a new download has to be added here.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.configuration_package import package_filename
from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.admin.test_settings_routes import PACKAGE_PASSPHRASE

SAFE_FILENAME = re.compile(r"^[a-z0-9.-]+$")
APP = Path(__file__).resolve().parents[2] / "app"
DOWNLOAD_MODULES = {
    "admin/audit_routes.py",
    "admin/branding_routes.py",
    "admin/idp_mapping_routes.py",
    "admin/settings_routes.py",
    "api/agent/routes.py",
}
EXPORTED = datetime(2026, 1, 1, tzinfo=UTC)


def _filename(header: str) -> str:
    match = re.fullmatch(r'attachment; filename="([^"]*)"', header)
    assert match, header
    return match.group(1)


def test_only_the_known_modules_set_a_content_disposition() -> None:
    found = {
        path.relative_to(APP).as_posix()
        for path in APP.rglob("*.py")
        if "content-disposition" in path.read_text(encoding="utf-8").lower()
    }

    assert found == DOWNLOAD_MODULES  # a new download: add its test below, then this set


@pytest.mark.parametrize(
    "download",
    [
        pytest.param(
            "/admin/audit/export.csv",
            marks=pytest.mark.xfail(
                strict=True,
                reason="Finding T8-2: the audit export is named audit-YYYYMMDDTHHMMSSZ.csv; "
                "the upper-case T and Z break ^[a-z0-9.-]+$ (harmless, but not the rule).",
            ),
        ),
        "/admin/organisations/demo/branding/theme.css",
        "/admin/idp-mapping/export.txt",
    ],
)
async def test_an_admin_download_has_a_safe_filename(site: Site, download: str) -> None:
    await site.login_as(SUPER)

    response = await site.client.get(download)

    assert response.status_code == 200
    assert SAFE_FILENAME.fullmatch(_filename(response.headers["content-disposition"]))


async def test_the_configuration_package_download_has_a_safe_filename(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        "/admin/settings/package/export",
        data={"passphrase": PACKAGE_PASSPHRASE, "passphrase_repeat": PACKAGE_PASSPHRASE},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 200
    assert SAFE_FILENAME.fullmatch(_filename(response.headers["content-disposition"]))


def test_the_package_filename_is_safe_for_the_default_installation() -> None:
    assert SAFE_FILENAME.fullmatch(package_filename("", EXPORTED))
    assert SAFE_FILENAME.fullmatch(package_filename("docker01", EXPORTED))


@pytest.mark.xfail(
    strict=True,
    reason="Finding T8-1: INSTALLATION_NAME allows upper case and underscores "
    "(^[A-Za-z0-9_.-]{0,64}$), so the package filename can break ^[a-z0-9.-]+$.",
)
@pytest.mark.parametrize("installation", ["Prod", "test_host", "ACC-1.Nl"])
def test_the_package_filename_is_safe_for_every_allowed_installation_name(
    installation: str,
) -> None:
    allowed = Settings(installation_name=installation).installation_name

    assert SAFE_FILENAME.fullmatch(package_filename(allowed, EXPORTED))
