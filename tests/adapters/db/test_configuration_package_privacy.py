"""What ADR 0089 promises about personal data and secrets in a configuration package (WP-B2).

The package is an installation export for the superuser: it holds every organisation, so there
is no "other tenant" to keep out. It promises two things, and this file tests exactly those:

* no personal data: the PII fields are on ``bookings`` (a state table, emptied and vacuumed),
  and no configuration table has a column of that name;
* no secret in clear text: a sealed value, and the portable login values, are readable only
  with the passphrase, never with the installation's key and never as bytes of the file.
"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.configuration_package import environment_values, export_package
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.models import Base
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.configuration_package import CONFIGURATION_TABLES
from app.domain.errors import DomainError, ErrorCode
from app.domain.privacy import PII_FIELDS
from app.domain.services import BookingService, ContactInput
from tests.adapters.db.test_configuration_package import NOW, PASSPHRASE, SOURCE_BOX, _path
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW as DOMAIN_NOW
from tests.support.domain import SLOT_0900, TEST_TOKEN_KEY, book_confirmed

PERSON = ContactInput(
    first_name="Zqpackfirst", last_name="Zqpacklast", email="zqpack@example.org", phone="0612345678"
)
CLEAR = {
    "credential": "zq-credential-token-7f3a",
    "login": "zq-oidc-client-secret-9c1d",
}
"""Secrets that must not appear as bytes of a package."""


def test_no_configuration_table_has_a_column_that_holds_personal_data() -> None:
    forbidden = PII_FIELDS
    columns = {
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        if table.name in CONFIGURATION_TABLES
        for column in table.columns
        if column.name in forbidden
    }

    assert columns == set(), "a personal-data column outside bookings travels in the package"


@pytest.fixture
async def package(engine: AsyncEngine, database_url: str, tmp_path: Path) -> bytes:
    """A package of an installation with a booking, a sealed credential and a login secret."""
    await seed_demo(engine)
    clock = FixedClock(DOMAIN_NOW)
    service = BookingService(SqlUnitOfWork(engine), clock, TEST_TOKEN_KEY)
    await book_confirmed(service, group_slug="demo", start=SLOT_0900, contact=PERSON, locale="nl")
    await CredentialStore(engine, SOURCE_BOX).put(
        "webex_service_app", {"t": CLEAR["credential"]}, NOW
    )
    return export_package(
        _path(database_url),
        SOURCE_BOX,
        PASSPHRASE,
        created_at=NOW,
        installation="bron",
        app_version="1.2.3",
        environment={"OIDC_CLIENT_SECRET": CLEAR["login"]},
        work_dir=tmp_path / "work",
    )


async def test_the_package_holds_no_booking_and_no_secret_in_clear_text(package: bytes) -> None:
    for text in (PERSON.first_name, PERSON.last_name, PERSON.email, *CLEAR.values()):
        assert text.encode() not in package, f"{text} is readable in the package"


async def test_the_login_secret_opens_with_the_passphrase_only(
    package: bytes, tmp_path: Path
) -> None:
    work = tmp_path / "work"

    assert environment_values(package, PASSPHRASE, work) == {"OIDC_CLIENT_SECRET": CLEAR["login"]}
    with pytest.raises(DomainError) as refused:
        environment_values(package, "the installation key is no passphrase", work)
    assert refused.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_WRONG
