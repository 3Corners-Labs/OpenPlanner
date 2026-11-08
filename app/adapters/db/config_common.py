"""What every reader and writer of a configuration document shares (ADR 0099 step 2).

One home for the writer signature, the refusal that names a field, the lookups of a parent by its
natural key, the time format of a document, and the one way a sealed secret is sealed and opened:
a secret that another settings key sealed is refused with
``integration_not_ready`` naming ``settings_key``, never left out of an export in silence.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from cryptography.exceptions import InvalidTag
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import OrganisationRow
from app.domain.configuration_changes import Change
from app.domain.configuration_values import Secret, Value
from app.domain.errors import DomainError, ErrorCode

type Writer = Callable[[AsyncSession, SecretBox | None, Change], Awaitable[None]]
"""Applies one planned change of one kind inside the caller's transaction."""


def invalid(field: str) -> DomainError:
    """Return the refusal that names ``field``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def need_box(box: SecretBox | None) -> SecretBox:
    """Return the box, or refuse: without a settings key no secret can be sealed or opened.

    Raises:
        DomainError: ``integration_not_ready`` naming ``settings_key``.
    """
    if box is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key")
    return box


async def row_or_refuse[Row](
    session: AsyncSession,
    statement: Select[Row],
    code: ErrorCode,
    field: str | None = None,
) -> Row:
    """Return the one row ``statement`` selects.

    Raises:
        DomainError: ``code`` (naming ``field`` when given) when there is none.
    """
    row = await session.scalar(statement)
    if row is None:
        raise DomainError(code, field) if field else DomainError(code)
    return row


async def find_organisation_id(session: AsyncSession, slug: str) -> int:
    """Return the id of the organisation ``slug``.

    Raises:
        DomainError: ``organisation_not_found`` when there is none.
    """
    found = await row_or_refuse(
        session,
        select(OrganisationRow.id).where(OrganisationRow.slug == slug),
        ErrorCode.ORGANISATION_NOT_FOUND,
        "organisation",
    )
    return int(found)


def moment(value: datetime | None) -> str | None:
    """Return a stored time as the UTC ISO text a document carries."""
    return None if value is None else value.astimezone(UTC).isoformat()


def seal(box: SecretBox | None, name: str, value: Value) -> tuple[bytes, bytes]:
    """Seal an opened secret under the record name ``name``.

    Raises:
        DomainError: ``integration_not_ready`` without a settings key; ``invalid_configuration``
            when the value is not an opened secret.
    """
    if not isinstance(value, Secret):
        raise invalid("secret")
    return need_box(box).seal(name, value.reveal().encode())


def opened(
    box: SecretBox | None, name: str, nonce: bytes | None, cipher: bytes | None
) -> Secret | None:
    """Return a sealed value opened as ``Secret``; ``None`` when nothing is set or no key is.

    Without a settings key nothing can be opened and the reader reports the secret as not set
    (the service refuses an export that asks for secrets then).

    Raises:
        DomainError: ``integration_not_ready`` when a value is set but the settings key does not
            open it.
    """
    if box is None or nonce is None or cipher is None:
        return None
    try:
        return Secret(box.open(name, nonce, cipher).decode())
    except InvalidTag:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key") from None
