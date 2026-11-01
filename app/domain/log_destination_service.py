"""Use cases of the log destinations (ADR 0085 step 2).

The superuser creates a destination, changes it, switches it on or off, sends a test line and
deletes it (``logging.manage``, locked, fresh login). The auditor sees the list and the
status of every process (``logging.view``). Every change is audited as the global event
``logging.destination`` with the kind and the names of the changed fields, never an address,
a credential or a platform's answer.

    overview / destination   logging.view     the configuration and the status, no credential
    create                   logging.manage   switched off, untested
    update                   logging.manage   a blank credential keeps the stored one
    set_enabled / delete     logging.manage
    send_test                logging.manage   one real delivery; the verdict is audited

The sockets are not opened here: the sender is an adapter (``LogDestinationSender``) and the
test goes through ``tester``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.log_destinations import (
    AUDIT_ACTION,
    DeliveryError,
    DestinationDraft,
    DestinationStatus,
    LogDestination,
    validate_draft,
    validate_secret,
)
from app.domain.ports import Clock, Transaction, UnitOfWork

_VIEW = Permission.LOGGING_VIEW
_EVERYTHING = Scope(everything=True)
"""A global permission has no narrower scope: once the matrix allowed it, it covers all."""
_MANAGE = Permission.LOGGING_MANAGE

_CHANGEABLE = (
    "name",
    "kind",
    "address",
    "index",
    "source",
    "min_level",
    "categories",
    "include_diagnostic",
    "include_trace",
    "ca_pem",
)
"""The fields of a draft an update may change."""


@dataclass(frozen=True)
class DestinationOverview:
    """Every destination with the status the processes reported."""

    destinations: list[LogDestination]
    status: dict[int, list[DestinationStatus]]
    """The status per destination id: one entry per process that reported."""


@dataclass(frozen=True)
class TestResult:
    """The verdict of *Send test line*."""

    __test__ = False  # not a pytest class

    ok: bool
    reason: str = ""
    code: int | None = None
    """The platform's status code, when it answered."""


class LogDestinationService:
    """Application service of the log destinations.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises and audits refusals.
        production: Whether this is a production installation, which refuses unencrypted
            addresses (``ENVIRONMENT``).
        tester: Sends one test line to a stored destination and raises ``DeliveryError``;
            ``None`` when the process cannot deliver, which refuses every test.
        on_change: Called after every change, so the senders reload at once.
    """

    def __init__(  # noqa: PLR0913
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        *,
        production: bool,
        tester: Callable[[int], Awaitable[None]] | None = None,
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._production = production
        self._tester = tester
        self._on_change = on_change

    # --- reading -------------------------------------------------------------------------

    async def overview(self, principal: Principal) -> DestinationOverview:
        """Return every destination with its status.

        Raises:
            DomainError: ``not_permitted``.
        """
        require(await self._admin.authorize(principal, _VIEW))
        scope = _EVERYTHING
        async with self._uow.read() as tx:
            destinations = await tx.list_log_destinations(scope)
            reported = await tx.list_log_destination_status(scope)
        status: dict[int, list[DestinationStatus]] = {}
        for entry in reported:
            status.setdefault(entry.destination_id, []).append(entry)
        return DestinationOverview(destinations, status)

    async def destination(
        self, principal: Principal, destination_id: int
    ) -> tuple[LogDestination, list[DestinationStatus]]:
        """Return one destination with the status of each process.

        Raises:
            DomainError: ``not_permitted`` or ``log_destination_not_found``.
        """
        require(await self._admin.authorize(principal, _VIEW))
        scope = _EVERYTHING
        async with self._uow.read() as tx:
            found = await _found(tx, destination_id)
            reported = [
                s
                for s in await tx.list_log_destination_status(scope)
                if s.destination_id == found.id
            ]
        return found, reported

    # --- changing ------------------------------------------------------------------------

    async def create(
        self, principal: Principal, draft: DestinationDraft, secret: str | None
    ) -> LogDestination:
        """Add a destination, switched off and untested.

        Raises:
            DomainError: ``not_permitted``, ``log_destination_invalid`` (the field),
                ``log_destination_insecure``, ``log_destination_name_taken`` or
                ``integration_not_ready`` (no settings key for the credential).
        """
        require(await self._admin.authorize(principal, _MANAGE))
        clean = validate_draft(draft, production=self._production)
        sealed = _checked_secret(clean, secret, stored=False)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            created = await tx.add_log_destination(
                LogDestination(
                    id=None,
                    name=clean.name,
                    kind=clean.kind,
                    address=clean.address,
                    index=clean.index,
                    source=clean.source,
                    min_level=clean.min_level,
                    categories=clean.categories,
                    include_diagnostic=clean.include_diagnostic,
                    include_trace=clean.include_trace,
                    enabled=False,
                    secret_set=sealed is not None,
                    ca_pem=clean.ca_pem,
                    created_at=now,
                    updated_at=now,
                ),
                sealed,
            )
            await self._audit(tx, principal, f"created:{created.kind}:{created.id}")
        self._on_change()
        return created

    async def update(
        self,
        principal: Principal,
        destination_id: int,
        draft: DestinationDraft,
        secret: str | None,
    ) -> list[str]:
        """Change a destination; return the names of the fields that changed.

        A blank credential keeps the stored one. Changing the kind, the address or the
        credential makes the destination untested again.

        Raises:
            DomainError: As ``create``, and ``log_destination_not_found``.
        """
        require(await self._admin.authorize(principal, _MANAGE))
        clean = validate_draft(draft, production=self._production)
        async with self._uow.locked() as tx:
            current = await _found(tx, destination_id)
            sealed = _checked_secret(clean, secret, stored=current.secret_set)
            changed = [f for f in _CHANGEABLE if _value(current, f) != _value(clean, f)]
            if sealed is not None:
                changed.append("secret")
            if not changed:
                return []
            retest = {"kind", "address", "secret", "ca_pem"} & set(changed)
            updated = replace(
                current,
                name=clean.name,
                kind=clean.kind,
                address=clean.address,
                index=clean.index,
                source=clean.source,
                min_level=clean.min_level,
                categories=clean.categories,
                include_diagnostic=clean.include_diagnostic,
                include_trace=clean.include_trace,
                ca_pem=clean.ca_pem,
                secret_set=current.secret_set and clean.kind.has_credential or sealed is not None,
                updated_at=self._clock.now(),
                tested_at=None if retest else current.tested_at,
            )
            await tx.save_log_destination(updated, sealed)
            await self._audit(
                tx, principal, f"changed:{updated.kind}:{destination_id}:{','.join(changed)}"
            )
        self._on_change()
        return changed

    async def set_enabled(self, principal: Principal, destination_id: int, enabled: bool) -> bool:
        """Switch the destination on or off; return whether that changed anything.

        Raises:
            DomainError: ``not_permitted`` or ``log_destination_not_found``.
        """
        require(await self._admin.authorize(principal, _MANAGE))
        async with self._uow.locked() as tx:
            current = await _found(tx, destination_id)
            if current.enabled == enabled:
                return False
            await tx.save_log_destination(
                replace(current, enabled=enabled, updated_at=self._clock.now()), None
            )
            verb = "enabled" if enabled else "disabled"
            await self._audit(tx, principal, f"{verb}:{current.kind}:{destination_id}")
        self._on_change()
        return True

    async def delete(self, principal: Principal, destination_id: int) -> None:
        """Remove the destination with its credential and status.

        Raises:
            DomainError: ``not_permitted`` or ``log_destination_not_found``.
        """
        require(await self._admin.authorize(principal, _MANAGE))
        async with self._uow.locked() as tx:
            current = await _found(tx, destination_id)
            await tx.delete_log_destination(destination_id)
            await self._audit(tx, principal, f"deleted:{current.kind}:{destination_id}")
        self._on_change()

    async def send_test(self, principal: Principal, destination_id: int) -> TestResult:
        """Send one test line now; audit the verdict, mark the destination tested on success.

        Raises:
            DomainError: ``not_permitted``, ``log_destination_not_found`` or
                ``integration_not_ready`` (this process cannot deliver).
        """
        require(await self._admin.authorize(principal, _MANAGE))
        if self._tester is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY)
        async with self._uow.read() as tx:
            current = await _found(tx, destination_id)
        result = TestResult(ok=True)
        try:
            await self._tester(destination_id)
        except DeliveryError as failed:
            result = TestResult(ok=False, reason=failed.reason.value, code=failed.code)
        async with self._uow.locked() as tx:
            stored = await tx.get_log_destination(destination_id)
            if stored is not None and result.ok:
                await tx.save_log_destination(replace(stored, tested_at=self._clock.now()), None)
            await self._audit(
                tx,
                principal,
                f"tested:{current.kind}:{destination_id}",
                Outcome.SUCCESS if result.ok else Outcome.FAILURE,
                result.reason,
            )
        return result

    # --- helpers -------------------------------------------------------------------------

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        detail: str,
        outcome: Outcome = Outcome.SUCCESS,
        reason: str = "",
    ) -> None:
        """Record a change in the same transaction, as a global event."""
        await tx.add_audit_event(
            AuditEvent(
                self._clock.now(),
                principal.subject,
                AUDIT_ACTION,
                outcome,
                reason,
                None,
                None,
                detail,
            )
        )


async def _found(tx: Transaction, destination_id: int) -> LogDestination:
    """Return the destination or raise ``log_destination_not_found``."""
    found = await tx.get_log_destination(destination_id)
    if found is None:
        raise DomainError(ErrorCode.LOG_DESTINATION_NOT_FOUND)
    return found


def _checked_secret(draft: DestinationDraft, secret: str | None, *, stored: bool) -> str | None:
    """Return the credential to seal, or ``None`` to keep the stored one.

    Raises:
        DomainError: ``log_destination_invalid`` (``secret``) for a credential the kind does
            not take or does not accept, or a missing one the kind needs.
    """
    text = (secret or "").strip()
    if text:
        return validate_secret(draft.kind, text)
    if draft.kind.needs_credential and not stored:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "secret")
    return None


def _value(source: LogDestination | DestinationDraft, field: str) -> object:
    """Return a changeable field of a destination or a draft."""
    return getattr(source, field)
