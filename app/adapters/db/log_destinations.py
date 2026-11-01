"""SQL queries of the log destinations (ADR 0085 step 2).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. The credential
is sealed with the ``SecretBox`` (ADR 0008) under ``logdest:<id>:secret``, which binds it to its
destination. The domain never sees it: it stores a credential and learns only that one is set.

``LogDestinationCredentialStore`` is the adapter side of the same table: the senders are built
from it (``app.adapters.log_shipping``), outside any domain transaction, once per reload.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import LogDestinationRow, LogDestinationStatusRow
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.log_destinations import (
    DestinationKind,
    DestinationStatus,
    LogDestination,
    State,
    categories_text,
    parse_categories,
)


def secret_name(destination_id: int) -> str:
    """Return the record name a credential is sealed under (associated data)."""
    return f"logdest:{destination_id}:secret"


def _destination(row: LogDestinationRow) -> LogDestination:
    """Map a row to the domain object; never the sealed credential."""
    return LogDestination(
        id=row.id,
        name=row.name,
        kind=DestinationKind(row.kind),
        address=row.address,
        index=row.index_name,
        source=row.source,
        min_level=row.min_level,
        categories=parse_categories(row.categories),
        include_diagnostic=row.include_diagnostic,
        include_trace=row.include_trace,
        enabled=row.enabled,
        secret_set=row.secret_ciphertext is not None,
        ca_pem=row.ca_pem,
        created_at=row.created_at,
        updated_at=row.updated_at,
        tested_at=row.tested_at,
    )


def _status(row: LogDestinationStatusRow) -> DestinationStatus:
    """Map a status row to the domain object."""
    return DestinationStatus(
        destination_id=row.destination_id,
        service=row.service,
        state=State(row.state),
        sent=row.sent,
        dropped=row.dropped,
        failed=row.failed,
        queued=row.queued,
        last_ok_at=row.last_ok_at,
        last_error_at=row.last_error_at,
        last_error=row.last_error,
        updated_at=row.updated_at,
    )


class LogDestinationQueries:
    """The log destinations' part of ``SqlTransaction``; needs ``_session`` and ``_box``."""

    _session: AsyncSession
    _box: SecretBox | None

    async def list_log_destinations(self, scope: Scope) -> list[LogDestination]:
        """Return the destinations of a scope that matches everything, ordered by name."""
        if not scope.everything:
            return []
        rows = (
            await self._session.scalars(select(LogDestinationRow).order_by(LogDestinationRow.name))
        ).all()
        return [_destination(row) for row in rows]

    async def get_log_destination(self, destination_id: int) -> LogDestination | None:
        """Return the destination with ``destination_id``."""
        row = await self._session.get(LogDestinationRow, destination_id)
        return _destination(row) if row else None

    async def add_log_destination(
        self, destination: LogDestination, secret: str | None
    ) -> LogDestination:
        """Store a new destination; the credential is sealed.

        Raises:
            DomainError: ``log_destination_name_taken``, or ``integration_not_ready`` when a
                credential is given without a box.
        """
        if await self._log_name_taken(destination.name, None):
            raise DomainError(ErrorCode.LOG_DESTINATION_NAME_TAKEN, "name")
        if secret is not None and self._box is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "secret")
        row = LogDestinationRow(created_at=destination.created_at)
        self._log_fill(row, destination)
        self._session.add(row)
        await self._session.flush()
        self._log_seal(row, secret)
        await self._session.flush()
        return _destination(row)

    async def save_log_destination(self, destination: LogDestination, secret: str | None) -> None:
        """Store the changes; ``secret=None`` keeps the stored credential.

        Raises:
            DomainError: ``log_destination_not_found``, ``log_destination_name_taken``, or
                ``integration_not_ready`` when a credential is given without a box.
        """
        row = await self._log_row(destination.id)
        if await self._log_name_taken(destination.name, row.id):
            raise DomainError(ErrorCode.LOG_DESTINATION_NAME_TAKEN, "name")
        if secret is not None and self._box is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "secret")
        self._log_fill(row, destination)
        row.tested_at = destination.tested_at
        self._log_seal(row, secret)
        await self._session.flush()

    async def delete_log_destination(self, destination_id: int) -> bool:
        """Remove the destination, its credential and its status; ``False`` if there was none."""
        row = await self._session.get(LogDestinationRow, destination_id)
        if row is None:
            return False
        await self._session.execute(
            delete(LogDestinationStatusRow).where(
                LogDestinationStatusRow.destination_id == destination_id
            )
        )
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def save_log_destination_status(self, status: DestinationStatus, at: datetime) -> None:
        """Store what a process reports about a destination; a deleted destination is skipped."""
        if await self._session.get(LogDestinationRow, status.destination_id) is None:
            return
        row = await self._session.get(
            LogDestinationStatusRow, (status.destination_id, status.service)
        )
        if row is None:
            row = LogDestinationStatusRow(
                destination_id=status.destination_id, service=status.service
            )
            self._session.add(row)
        row.state = status.state.value
        row.sent = status.sent
        row.dropped = status.dropped
        row.failed = status.failed
        row.queued = status.queued
        row.last_ok_at = status.last_ok_at
        row.last_error_at = status.last_error_at
        row.last_error = status.last_error
        row.updated_at = at
        await self._session.flush()

    async def list_log_destination_status(self, scope: Scope) -> list[DestinationStatus]:
        """Return the status every process reported, ordered by destination and service."""
        if not scope.everything:
            return []
        rows = (
            await self._session.scalars(
                select(LogDestinationStatusRow).order_by(
                    LogDestinationStatusRow.destination_id, LogDestinationStatusRow.service
                )
            )
        ).all()
        return [_status(row) for row in rows]

    async def _log_row(self, destination_id: int | None) -> LogDestinationRow:
        """Return the row of ``destination_id``.

        Raises:
            DomainError: ``log_destination_not_found``.
        """
        row = await self._session.get(LogDestinationRow, destination_id or 0)
        if row is None:
            raise DomainError(ErrorCode.LOG_DESTINATION_NOT_FOUND)
        return row

    async def _log_name_taken(self, name: str, own_id: int | None) -> bool:
        """Return whether another destination has ``name``."""
        found = await self._session.scalar(
            select(LogDestinationRow.id).where(LogDestinationRow.name == name)
        )
        return found is not None and found != own_id

    @staticmethod
    def _log_fill(row: LogDestinationRow, destination: LogDestination) -> None:
        """Copy the public configuration onto the row."""
        row.name = destination.name
        row.kind = destination.kind.value
        row.address = destination.address
        row.index_name = destination.index
        row.source = destination.source
        row.min_level = destination.min_level
        row.categories = categories_text(destination.categories)
        row.include_diagnostic = destination.include_diagnostic
        row.include_trace = destination.include_trace
        row.enabled = destination.enabled
        row.ca_pem = destination.ca_pem
        row.updated_at = destination.updated_at

    def _log_seal(self, row: LogDestinationRow, secret: str | None) -> None:
        """Seal ``secret`` onto the row; ``None`` leaves the stored one."""
        if secret is None or self._box is None:
            return
        row.secret_nonce, row.secret_ciphertext = self._box.seal(
            secret_name(row.id), secret.encode()
        )


@dataclass(frozen=True)
class LogTarget:
    """A destination with its opened credential, for the sender of one process.

    Attributes:
        destination: The public configuration.
        secret: The opened credential; ``None`` when none is stored or none could be opened.
        unreadable: Whether a credential is stored but could not be opened (no settings key).
    """

    destination: LogDestination
    secret: str | None
    unreadable: bool = False


class LogDestinationCredentialStore:
    """Opens the credentials of the enabled destinations for the senders.

    Args:
        engine: The database engine.
        box: Opens the sealed values; ``None`` when no settings key is configured, which
            leaves every destination that needs a credential without one.
    """

    def __init__(self, engine: AsyncEngine, box: SecretBox | None) -> None:
        """Store the collaborators."""
        self._engine = engine
        self._box = box

    async def enabled(self) -> list[LogTarget]:
        """Return the enabled destinations with their opened credentials."""
        async with AsyncSession(self._engine) as session:
            rows = (
                await session.scalars(
                    select(LogDestinationRow)
                    .where(LogDestinationRow.enabled.is_(True))
                    .order_by(LogDestinationRow.id)
                )
            ).all()
        return [self._open(row) for row in rows]

    async def one(self, destination_id: int) -> LogTarget | None:
        """Return one destination, enabled or not, for *Send test line*."""
        async with AsyncSession(self._engine) as session:
            row = await session.get(LogDestinationRow, destination_id)
        return self._open(row) if row else None

    def _open(self, row: LogDestinationRow) -> LogTarget:
        """Open the credential of ``row``; a failure is reported, never raised."""
        destination = _destination(row)
        if row.secret_ciphertext is None or row.secret_nonce is None:
            return LogTarget(destination, None)
        if self._box is None:
            return LogTarget(destination, None, unreadable=True)
        try:
            secret = self._box.open(
                secret_name(row.id), row.secret_nonce, row.secret_ciphertext
            ).decode()
        except Exception:  # a wrong key or a tampered value; the reason stays out of the log
            return LogTarget(destination, None, unreadable=True)
        return LogTarget(destination, secret)
