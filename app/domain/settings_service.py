"""Changing and resetting platform settings in the portal and from the host (ADR 0042 step 4).

::

    superuser / CLI ─► change(key, text) ─► editable? ─► locked by env? ─► parse within bounds
                                         ─► rules on the combination ─► store, version + 1, audit

Every change and every refusal is audited with the key; a change also with the old and the
new value (ADR 0042 §5). Setting values are numbers, rates and codes, never personal data.
``LOG_LEVEL`` set here always expires after at most ``LOG_LEVEL_MAX_EXPIRY_HOURS`` (§6).

This step covers operations, retention, storage, rate limits, admin sessions, staff and the
planner. The fallback mail server, Webex and updates follow with their secrets (step 6), the
login mapping with its dry run (step 7), and organisation settings in step 5.
"""

from collections.abc import Callable, Collection, Mapping
from datetime import datetime, timedelta

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.platform_settings import usable
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.settings_registry import (
    BY_KEY,
    LOG_LEVEL_MAX_EXPIRY_HOURS,
    Kind,
    SettingClass,
    SettingSpec,
    Topic,
    parse_rate,
)

CLI_ACTOR = "system:cli"
"""The actor of changes made on the host (ADR 0042 §10)."""

EDITABLE_TOPICS = frozenset(
    {
        Topic.OPERATIONS,
        Topic.RETENTION,
        Topic.STORAGE,
        Topic.RATE_LIMITS,
        Topic.ADMIN_SESSION,
        Topic.STAFF,
        Topic.PLANNER,
    }
)
"""The topics of class C settings the portal changes from build step 4 on."""

LOG_LEVEL = "log_level"

_PERMISSION = Permission.SETTINGS_PLATFORM_MANAGE
_TRUE = frozenset({"true", "on", "yes", "1"})
_FALSE = frozenset({"false", "off", "no", "0"})


def editable(spec: SettingSpec) -> bool:
    """Return whether the portal and the CLI may change ``spec`` in this build step."""
    return (
        spec.class_ is SettingClass.PLATFORM and not spec.secret and spec.topic in EDITABLE_TOPICS
    )


def parse(spec: SettingSpec, raw: str) -> object:
    """Turn submitted text into the registry's value, within the portal bounds.

    Raises:
        DomainError: ``setting_invalid`` with the key as field (never the text).
    """
    text = raw.strip()
    value: object
    try:
        if not text:
            value = None
        elif spec.kind is Kind.INT:
            value = int(text)
        elif spec.kind is Kind.FLOAT:
            value = float(text)
        elif spec.kind is Kind.BOOL:
            value = _bool(text.lower())
        elif spec.kind is Kind.ENUM:
            value = next((c for c in spec.choices if c.lower() == text.lower()), text)
        elif spec.kind is Kind.RATE:
            rate = parse_rate(text)
            value = f"{rate[0]}/{rate[1]}" if rate else text
        else:
            value = text
    except ValueError:
        raise DomainError(ErrorCode.SETTING_INVALID, spec.key) from None
    if not usable(spec, value):
        raise DomainError(ErrorCode.SETTING_INVALID, spec.key)
    return value


def _bool(text: str) -> bool:
    """Read a yes/no value.

    Raises:
        ValueError: For anything else.
    """
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(text)


def _known(key: str) -> str:
    """Return ``key`` for the audit detail if it is a registered setting, else ``unknown``."""
    return key if key in BY_KEY else "unknown"


def _text(value: object) -> str:
    """Write a value for the audit detail: plain, never more than a short token."""
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)[:64]


class SettingsService:
    """Use cases of changing class C settings.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises the superuser and audits refusals.
        locked: Returns the keys the environment locks (never changed here).
        combination: Returns the rule that the proposed portal values break together (the
            cross-field rules of the settings), or ``None``.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        *,
        locked: Callable[[], Collection[str]],
        combination: Callable[[Mapping[str, object]], str | None],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._locked = locked
        self._combination = combination

    # --- superuser -----------------------------------------------------------------------

    async def change(
        self, principal: Principal, key: str, raw: str, *, hours: int | None = None
    ) -> None:
        """Change a setting in the portal; ``hours`` is the expiry of ``LOG_LEVEL``.

        Raises:
            DomainError: ``not_permitted``, ``setting_not_editable``, ``setting_locked``,
                ``setting_invalid``, ``setting_expiry`` or ``setting_combination``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        try:
            await self._change(principal.subject, key, raw, hours)
        except DomainError as error:
            await self._refused(principal.subject, error, f"set:{_known(key)}")
            raise

    async def reset(self, principal: Principal, key: str) -> bool:
        """Remove the portal value, so the default applies; return whether there was one.

        Raises:
            DomainError: ``not_permitted``, ``setting_not_editable``, ``setting_locked`` or
                ``setting_combination``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        try:
            return await self._reset(principal.subject, [key])
        except DomainError as error:
            await self._refused(principal.subject, error, f"reset:{_known(key)}")
            raise

    # --- host ----------------------------------------------------------------------------

    async def change_host(self, key: str, raw: str, *, hours: int | None = None) -> None:
        """Change a setting from the host CLI, audited as ``system:cli``.

        Raises:
            DomainError: As ``change``, apart from ``not_permitted``.
        """
        await self._change(CLI_ACTOR, key, raw, hours)

    async def reset_host(self, key: str) -> bool:
        """Remove one portal value from the host; return whether there was one.

        Raises:
            DomainError: ``setting_not_editable``, ``setting_locked`` or
                ``setting_combination``.
        """
        return await self._reset(CLI_ACTOR, [key])

    async def reset_all_host(self) -> int:
        """Remove every portal value this step can change; return how many were removed."""
        async with self._uow.read() as tx:
            stored = await tx.platform_settings()
        keys = [k for k in stored if k in BY_KEY and editable(BY_KEY[k])]
        removed = 0
        for key in keys:
            removed += await self._reset(CLI_ACTOR, [key])
        return removed

    # --- shared --------------------------------------------------------------------------

    def _spec(self, key: str) -> SettingSpec:
        """Return the entry of a key this step may change.

        Raises:
            DomainError: ``setting_not_editable`` or ``setting_locked``.
        """
        spec = BY_KEY.get(key)
        if spec is None or not editable(spec):
            raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, key if spec else None)
        if key in self._locked():
            raise DomainError(ErrorCode.SETTING_LOCKED, key)
        return spec

    async def _change(self, actor: str, key: str, raw: str, hours: int | None) -> None:
        """Validate and store one value, then audit it."""
        spec = self._spec(key)
        value = parse(spec, raw)
        now = self._clock.now()
        expires = self._expiry(spec, hours, now)
        async with self._uow.locked() as tx:
            stored = await tx.platform_settings()
            if self._combination({**stored, key: value}):
                raise DomainError(ErrorCode.SETTING_COMBINATION, key)
            old = stored.get(key, spec.default)
            await tx.save_platform_setting(key, value, now, expires)
            await tx.bump_settings_version()
            await _audit(tx, now, actor, f"set:{key}:{_text(old)}->{_text(value)}")

    async def _reset(self, actor: str, keys: list[str]) -> bool:
        """Remove the values of ``keys`` (one, in practice), then audit it."""
        for key in keys:
            self._spec(key)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            stored = await tx.platform_settings()
            present = [k for k in keys if k in stored]
            if not present:
                return False
            rest = {k: v for k, v in stored.items() if k not in present}
            if self._combination(rest):
                raise DomainError(ErrorCode.SETTING_COMBINATION, present[0])
            for key in present:
                await tx.delete_platform_setting(key)
                await _audit(tx, now, actor, f"reset:{key}:{_text(stored[key])}")
            await tx.bump_settings_version()
        return True

    @staticmethod
    def _expiry(spec: SettingSpec, hours: int | None, now: datetime) -> datetime | None:
        """Return when the value lapses: only ``LOG_LEVEL``, after 1 to 24 hours.

        Raises:
            DomainError: ``setting_expiry``.
        """
        if spec.key != LOG_LEVEL:
            if hours is not None:
                raise DomainError(ErrorCode.SETTING_EXPIRY, spec.key)
            return None
        if hours is None or not 1 <= hours <= LOG_LEVEL_MAX_EXPIRY_HOURS:
            raise DomainError(ErrorCode.SETTING_EXPIRY, spec.key)
        return now + timedelta(hours=hours)

    async def _refused(self, actor: str, error: DomainError, detail: str) -> None:
        """Audit a refused change with its code and the key, never the submitted text."""
        await self._admin.record(
            actor, _PERMISSION.value, Outcome.DENIED, error.code.value, detail=detail
        )


async def _audit(tx: Transaction, at: datetime, actor: str, detail: str) -> None:
    """Record a change: global, so only the superuser sees it in the audit log."""
    await tx.add_audit_event(
        AuditEvent(at, actor, _PERMISSION.value, Outcome.SUCCESS, detail=detail)
    )
