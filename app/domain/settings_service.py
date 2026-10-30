"""Changing and resetting platform settings in the portal and from the host (ADR 0042 step 4).

::

    superuser / CLI ─► change(key, text) ─► editable? ─► locked by env? ─► parse within bounds
                                         ─► rules on the combination ─► store, version + 1, audit

Every change and every refusal is audited with the key; a change also with the old and the
new value (ADR 0042 §5). Setting values are numbers, rates and codes, never personal data.
``LOG_LEVEL`` set here always expires after at most ``LOG_LEVEL_MAX_EXPIRY_HOURS`` (§6).

This covers operations, retention, storage, rate limits, admin sessions, staff and the
planner, and the platform default of every class D setting. An organisation admin sets a
class D value for their own organisation (``change_organisation``, step 5); the environment
lock binds only the platform value, never an organisation (§2). The fallback mail server,
Webex and updates follow with their secrets (step 6), the login mapping with its dry run
(step 7).
"""

from collections.abc import Callable, Collection, Mapping
from datetime import datetime, timedelta

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.errors import DomainError, ErrorCode
from app.domain.platform_settings import usable
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Organisation
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
        Topic.MAIL,
        Topic.UPDATES,
        Topic.CONFIRMATION,
    }
)
"""The topics of class C settings the portal changes: step 4, and mail and updates with their
secrets from step 6 (sealed, ADR 0008). Webex/WxCC and the login mapping follow."""

LOG_LEVEL = "log_level"

_PERMISSION = Permission.SETTINGS_PLATFORM_MANAGE
_ORGANISATION = Permission.SETTINGS_ORGANISATION_MANAGE
_TRUE = frozenset({"true", "on", "yes", "1"})
_FALSE = frozenset({"false", "off", "no", "0"})


def editable(spec: SettingSpec) -> bool:
    """Return whether the portal and the CLI may change ``spec``'s platform value."""
    if spec.class_ is SettingClass.ORGANISATION:
        return not spec.secret
    return spec.class_ is SettingClass.PLATFORM and spec.topic in EDITABLE_TOPICS


def parse(spec: SettingSpec, raw: str) -> object:
    """Turn submitted text into the registry's value, within the portal bounds.

    Raises:
        DomainError: ``setting_invalid`` with the key as field (never the text).
    """
    text = raw.strip()
    if spec.secret:
        if not text:
            raise DomainError(ErrorCode.SETTING_INVALID, spec.key)
        return text
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
        organisation_combination: The same for one organisation's proposed class D values
            over the platform values; defaults to ``combination`` on those values alone.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        *,
        locked: Callable[[], Collection[str]],
        combination: Callable[[Mapping[str, object]], str | None],
        organisation_combination: Callable[[int, Mapping[str, object]], str | None] | None = None,
        environment_value: Callable[[str], object] = lambda _: None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._locked = locked
        self._combination = combination
        self._environment_value = environment_value
        self._organisation_combination = organisation_combination or (
            lambda _, proposed: combination(proposed)
        )

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

    # --- organisation admin --------------------------------------------------------------

    async def organisations(self, principal: Principal) -> list[Organisation]:
        """Return the organisations whose settings ``principal`` may change, by slug."""
        async with self._uow.read() as tx:
            return await tx.list_organisations(scope_for(principal, _ORGANISATION))

    async def organisation(self, principal: Principal, slug: str) -> Organisation:
        """Return one organisation whose settings ``principal`` may change.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        require(await self._admin.authorize(principal, _ORGANISATION, organisation_slug=slug))
        async with self._uow.read() as tx:
            found = await tx.get_organisation(slug)
        if found is None:
            raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
        return found

    async def change_organisation(
        self, principal: Principal, organisation: str, key: str, raw: str
    ) -> None:
        """Set one organisation's value of a class D setting.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``setting_not_editable``, ``setting_invalid`` or ``setting_combination``.
        """
        require(
            await self._admin.authorize(principal, _ORGANISATION, organisation_slug=organisation)
        )
        try:
            spec = _class_d(key)
            value = parse(spec, raw)
            now = self._clock.now()
            async with self._uow.locked() as tx:
                organisation_id = await _organisation_id(tx, organisation)
                stored = (await tx.organisation_settings()).get(organisation_id, {})
                if self._organisation_combination(organisation_id, {**stored, key: value}):
                    raise DomainError(ErrorCode.SETTING_COMBINATION, key)
                old = stored.get(key, spec.default)
                await tx.save_organisation_setting(organisation_id, key, value, now)
                await tx.bump_settings_version()
                detail = f"set:{key}:{_text(old)}->{_text(value)}"
                await _audit(tx, now, principal.subject, detail, _ORGANISATION, organisation)
        except DomainError as error:
            await self._refused(
                principal.subject, error, f"set:{_known(key)}", _ORGANISATION, organisation
            )
            raise

    async def reset_organisation(self, principal: Principal, organisation: str, key: str) -> bool:
        """Remove one organisation's value, so the platform value applies; return whether set.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``,
                ``setting_not_editable`` or ``setting_combination``.
        """
        require(
            await self._admin.authorize(principal, _ORGANISATION, organisation_slug=organisation)
        )
        try:
            _class_d(key)
            now = self._clock.now()
            async with self._uow.locked() as tx:
                organisation_id = await _organisation_id(tx, organisation)
                stored = (await tx.organisation_settings()).get(organisation_id, {})
                if key not in stored:
                    return False
                rest = {k: v for k, v in stored.items() if k != key}
                if self._organisation_combination(organisation_id, rest):
                    raise DomainError(ErrorCode.SETTING_COMBINATION, key)
                await tx.delete_organisation_setting(organisation_id, key)
                await tx.bump_settings_version()
                detail = f"reset:{key}:{_text(stored[key])}"
                await _audit(tx, now, principal.subject, detail, _ORGANISATION, organisation)
            return True
        except DomainError as error:
            await self._refused(
                principal.subject, error, f"reset:{_known(key)}", _ORGANISATION, organisation
            )
            raise

    # --- host ----------------------------------------------------------------------------

    async def change_host(self, key: str, raw: str, *, hours: int | None = None) -> None:
        """Change a setting from the host CLI, audited as ``system:cli``.

        Raises:
            DomainError: As ``change``, apart from ``not_permitted``.
        """
        spec = BY_KEY.get(key)
        if spec is not None and spec.secret:  # ADR 0042 §10: secrets never through the CLI
            raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, key)
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
        if spec.secret:
            await self._seal(actor, spec, str(value), "set")
            return
        async with self._uow.locked() as tx:
            stored = await tx.platform_settings()
            if self._combination({**stored, key: value}):
                raise DomainError(ErrorCode.SETTING_COMBINATION, key)
            old = stored.get(key, spec.default)
            await tx.save_platform_setting(key, value, now, expires)
            await tx.bump_settings_version()
            await _audit(tx, now, actor, f"set:{key}:{_text(old)}->{_text(value)}")

    async def _seal(self, actor: str, spec: SettingSpec, value: str, verb: str) -> None:
        """Seal one secret and audit that it was set, never its value.

        Raises:
            DomainError: ``setting_no_key`` without ``SETTINGS_ENCRYPTION_KEY``.
        """
        now = self._clock.now()
        async with self._uow.locked() as tx:
            if not await tx.secrets_available():
                raise DomainError(ErrorCode.SETTING_NO_KEY, spec.key)
            await tx.save_platform_secret(spec.key, value, now)
            await tx.bump_settings_version()
            await _audit(tx, now, actor, f"{verb}:{spec.key}:secret")

    async def _reset(self, actor: str, keys: list[str]) -> bool:
        """Remove the values of ``keys`` (one, in practice), then audit it."""
        specs = [self._spec(key) for key in keys]
        if any(spec.secret for spec in specs):
            return await self._reset_secrets(actor, keys)
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

    async def _reset_secrets(self, actor: str, keys: list[str]) -> bool:
        """Remove sealed portal secrets, then audit it without a value."""
        now = self._clock.now()
        removed = False
        async with self._uow.locked() as tx:
            for key in keys:
                if await tx.delete_platform_secret(key):
                    removed = True
                    await _audit(tx, now, actor, f"reset:{key}:secret")
            if removed:
                await tx.bump_settings_version()
        return removed

    async def adopt(self, principal: Principal, key: str) -> None:
        """Take a value the environment locks over into the portal (ADR 0042 §12).

        A secret is sealed by the server; the superuser never sees it. The setting stays
        locked until the operator removes the variable and restarts the containers.

        Raises:
            DomainError: ``not_permitted``, ``setting_not_editable``, ``setting_not_locked``,
                ``setting_invalid`` or ``setting_no_key``.
        """
        require(await self._admin.authorize(principal, _PERMISSION))
        try:
            await self._adopt(principal.subject, key)
        except DomainError as error:
            await self._refused(principal.subject, error, f"adopt:{_known(key)}")
            raise

    async def _adopt(self, actor: str, key: str) -> None:
        """Copy the environment's value of a locked setting into the portal."""
        spec = BY_KEY.get(key)
        if spec is None or not editable(spec):
            raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, key if spec else None)
        if key not in self._locked():
            raise DomainError(ErrorCode.SETTING_NOT_LOCKED, key)
        value = self._environment_value(key)
        if spec.secret:
            if not isinstance(value, str) or not value:
                raise DomainError(ErrorCode.SETTING_INVALID, key)
            await self._seal(actor, spec, value, "adopt")
            return
        if not usable(spec, value):
            raise DomainError(ErrorCode.SETTING_INVALID, key)
        now = self._clock.now()
        async with self._uow.locked() as tx:
            await tx.save_platform_setting(key, value, now, None)
            await tx.bump_settings_version()
            await _audit(tx, now, actor, f"adopt:{key}:{_text(value)}")

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

    async def _refused(
        self,
        actor: str,
        error: DomainError,
        detail: str,
        permission: Permission = _PERMISSION,
        organisation: str | None = None,
    ) -> None:
        """Audit a refused change with its code and the key, never the submitted text."""
        known = organisation if error.code is not ErrorCode.ORGANISATION_NOT_FOUND else None
        await self._admin.record(
            actor, permission.value, Outcome.DENIED, error.code.value, known, detail=detail
        )


def _class_d(key: str) -> SettingSpec:
    """Return the entry of a class D setting.

    Raises:
        DomainError: ``setting_not_editable`` for anything else.
    """
    spec = BY_KEY.get(key)
    if spec is None or spec.class_ is not SettingClass.ORGANISATION:
        raise DomainError(ErrorCode.SETTING_NOT_EDITABLE, key if spec else None)
    return spec


async def _organisation_id(tx: Transaction, slug: str) -> int:
    """Return the id of the organisation with ``slug``.

    Raises:
        DomainError: ``organisation_not_found``.
    """
    found = await tx.get_organisation(slug)
    if found is None:
        raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
    return found.id


async def _audit(
    tx: Transaction,
    at: datetime,
    actor: str,
    detail: str,
    permission: Permission = _PERMISSION,
    organisation: str | None = None,
) -> None:
    """Record a change: platform values for the superuser only, an organisation's for its own."""
    await tx.add_audit_event(
        AuditEvent(
            at, actor, permission.value, Outcome.SUCCESS, organisation=organisation, detail=detail
        )
    )
