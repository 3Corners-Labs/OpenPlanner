"""Export, plan and apply of a configuration document (ADR 0099 points 1, 6, 11 and 12).

The use cases behind the host command, the portal and the machine API. *Export* writes the
installation's configuration (or one organisation's) as a document, with its secrets sealed
under a passphrase or left out. *Plan* reads a document and returns, without writing, what
applying it would change and what refuses it. *Apply* plans again inside the write lock, with
the configuration read again, and writes the plan in one transaction, or nothing when anything
refuses it; the plan hash it is given must still be the plan's. Rights come from the matrix
per change (``configuration_plan``); every applied change is an audit event with its natural
key and action, never a value.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Protocol

from app.domain.admin import AdminService
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Permission, Principal, Target, decide
from app.domain.configuration_changes import (
    Change,
    ChangeAction,
    ConfigurationSnapshot,
    Refusal,
    RuleContext,
)
from app.domain.configuration_document import Document, Mode, Scope, parse_document, to_json
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_schema import Format
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS, V1
from app.domain.configuration_values import Json
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, UnitOfWork

EXPORT_DETAIL = "configuration.export"
APPLY_DETAIL = "configuration.apply"
ISSUER_KIND = "identity_provider"
DISCOVERY_FIELDS = frozenset({"discovery_url", "client_auth"})
PERSONAL_KINDS = frozenset({"platform_alert_target", "organisation_alert_target"})
"""Kinds whose natural key is an address; the audit names such an object by a short digest."""

type IssuerOf = Callable[[str, str], Awaitable[str]]
"""Fetches and checks a discovery document: (discovery URL, client auth) to the issuer."""


class DocumentCodec(Protocol):
    """Turns bytes into the JSON of a document and back (``app.adapters.configuration_json``)."""

    def decode(self, data: bytes, max_bytes: int) -> object:
        """Return the JSON in ``data``; refuse what is not one document."""
        ...

    def encode(self, document: Json) -> bytes:
        """Return the text of a document."""
        ...


class DocumentSealing(Protocol):
    """Seals and opens the secrets of a document (``app.adapters.configuration_seal``)."""

    def seal(self, document: Document, passphrase: str) -> Document:
        """Return the document with its opened secrets sealed under ``passphrase``."""
        ...

    def open(self, document: Document, passphrase: str | None) -> Document:
        """Return the document with its sealed secrets opened."""
        ...

    def strip(self, document: Document) -> Document:
        """Return the document without any secret."""
        ...


@dataclass(frozen=True)
class Actor:
    """Who acts: a person with a principal, or the host (the CLI), which may do everything.

    Attributes:
        subject: The audit subject (the identity provider's subject, or ``system:cli``).
        principal: The person's rights; ``None`` for the host.
    """

    subject: str
    principal: Principal | None = None

    def may_change(self, permission: Permission, target: Target) -> bool:
        """Whether the actor holds ``permission`` on ``target``."""
        return self.principal is None or decide(self.principal, permission, target).allowed


@dataclass(frozen=True)
class ApplyResult:
    """What an apply did.

    Attributes:
        plan: The plan that was computed inside the write lock.
        version: The new settings version, or ``None`` when nothing was written.
    """

    plan: Plan
    version: int | None

    @property
    def applied(self) -> bool:
        """Whether the plan was written."""
        return self.version is not None


class ConfigurationDocumentService:
    """Exports, plans and applies configuration documents.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Writes the audit events of an export.
        codec: Turns bytes into JSON and back.
        sealing: Seals and opens secrets under a passphrase.
        installation: ``INSTALLATION_NAME``, written into an export's header.
        app_version: The running release, written into an export's header.
        context: The rules' view of the installation (limits, production flag, claim values).
        locked: The settings the environment locks.
        max_bytes: The largest document accepted (``configuration_package_max_mb``).
        max_changes: The most changes one apply may hold (``configuration_apply_max_changes``).
        issuer_of: Fetches a provider's discovery document; needed to create or move one.
        formats: The format versions this release reads.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        codec: DocumentCodec,
        sealing: DocumentSealing,
        *,
        installation: str,
        app_version: str,
        context: Callable[[], RuleContext],
        locked: Callable[[], frozenset[str]],
        max_bytes: Callable[[], int],
        max_changes: Callable[[], int],
        issuer_of: IssuerOf | None = None,
        formats: Mapping[str, Format] = FORMATS,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._codec = codec
        self._sealing = sealing
        self._installation = installation
        self._app_version = app_version
        self._context = context
        self._locked = locked
        self._max_bytes = max_bytes
        self._max_changes = max_changes
        self._issuer_of = issuer_of
        self._formats = formats

    async def export(
        self, actor: Actor, scope: Scope, passphrase: str | None, *, secrets: bool
    ) -> bytes:
        """Write the configuration of ``scope`` as a document; audited.

        With ``secrets`` the passphrase seals them (at least 16 characters); without, every
        secret is left out and no passphrase is needed.

        Raises:
            DomainError: ``configuration_passphrase_short``; ``integration_not_ready`` when
                secrets are wanted and the installation has no settings key.
        """
        if secrets and (passphrase is None or len(passphrase) < 16):
            raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_SHORT, "passphrase")
        async with self._uow.read() as tx:
            snapshot = await tx.read_configuration(self._clock.now(), self._locked())
        document = Document(
            format=V1.name,
            mode=Mode.MERGE,
            scope=scope,
            root=_confined(snapshot.root, scope),
            digest="",
            schema=V1,
            meta={
                "installation": self._installation,
                "exported_at": self._clock.now().isoformat(),
                "app_version": self._app_version,
            },
        )
        if secrets and passphrase is not None:
            document = self._sealing.seal(document, passphrase)
        else:
            document = self._sealing.strip(document)
        permission = _route_permission(scope)
        detail = EXPORT_DETAIL + (",secrets" if secrets else "")
        await self._admin.record(
            actor.subject, permission.value, Outcome.SUCCESS, "", scope.organisation, None, detail
        )
        return self._codec.encode(to_json(document))

    async def plan(
        self,
        actor: Actor,
        data: bytes,
        passphrase: str | None,
        *,
        scope: Scope | None = None,
    ) -> Plan:
        """Plan a document without writing anything.

        Args:
            actor: Who asks.
            data: The document.
            passphrase: Opens its sealed secrets, if it has any.
            scope: The scope the caller allows; a document of another scope is refused.

        Raises:
            DomainError: The structure codes of ``parse_document``,
                ``configuration_package_*`` and ``configuration_passphrase_*``,
                ``configuration_out_of_scope``.
        """
        document = self._read(data, passphrase, scope)
        async with self._uow.read() as tx:
            snapshot = await tx.read_configuration(self._clock.now(), self._locked())
        return self._plan(actor, document, snapshot)

    async def apply(
        self,
        actor: Actor,
        data: bytes,
        passphrase: str | None,
        plan_hash: str | None,
        *,
        scope: Scope | None = None,
    ) -> ApplyResult:
        """Apply a document: plan again inside the write lock, then write all or nothing.

        ``plan_hash`` is the hash of the plan the caller reviewed. It is required when the
        plan deletes anything, and refused when it is not the plan's.

        Raises:
            DomainError: As ``plan``; ``configuration_plan_stale`` when the hash is missing
                for a plan with deletions or no longer the plan's;
                ``configuration_too_many_changes``.
        """
        document = self._read(data, passphrase, scope)
        async with self._uow.read() as tx:
            first = self._plan(
                actor, document, await tx.read_configuration(self._clock.now(), self._locked())
            )
        if not first.ok:
            return await self._refused(actor, document, first)
        issuers, refusals = await self._issuers(first)
        if refusals:
            return await self._refused(actor, document, replace(first, refusals=refusals))
        async with self._uow.locked() as tx:
            snapshot = await tx.read_configuration(self._clock.now(), self._locked())
            plan = self._plan(actor, document, snapshot)
            problem = self._problem(plan, first, plan_hash)
            if plan.refusals or problem is not None:
                result = ApplyResult(plan, None)
            else:
                version = await tx.apply_configuration(_enriched(plan.changes, issuers))
                for change in plan.changes:
                    await tx.add_audit_event(self._event(actor, plan, change))
                result = ApplyResult(plan, version)
        if problem is not None:
            await self._record_refusal(actor, document, problem)
            raise DomainError(problem)
        if not result.applied:
            await self._record_refusal(actor, document, ErrorCode.INVALID_CONFIGURATION)
        return result

    # --- internals -----------------------------------------------------------------------

    def _read(self, data: bytes, passphrase: str | None, scope: Scope | None) -> Document:
        """Decode, parse, confine and open a document."""
        document = parse_document(self._codec.decode(data, self._max_bytes()), self._formats)
        if scope is not None and document.scope != scope:
            raise DomainError(ErrorCode.CONFIGURATION_OUT_OF_SCOPE, "scope")
        return self._sealing.open(document, passphrase)

    def _plan(self, actor: Actor, document: Document, snapshot: ConfigurationSnapshot) -> Plan:
        """Plan ``document`` against a snapshot of the configuration."""
        return plan_document(
            document,
            snapshot.root,
            usage=snapshot.usage,
            may_change=actor.may_change,
            context=replace(self._context(), now=self._clock.now()),
            settings_version=snapshot.version,
        )

    def _problem(self, plan: Plan, first: Plan, plan_hash: str | None) -> ErrorCode | None:
        """Return why an otherwise valid plan may not be written, or ``None``."""
        if plan.refusals:
            return None
        if len(plan.changes) > self._max_changes():
            return ErrorCode.CONFIGURATION_TOO_MANY_CHANGES
        deletes = any(c.action is ChangeAction.DELETE for c in plan.changes)
        if plan.digest != first.digest:
            return ErrorCode.CONFIGURATION_PLAN_STALE
        if plan_hash is None and deletes:
            return ErrorCode.CONFIGURATION_PLAN_STALE
        if plan_hash is not None and plan_hash != plan.digest:
            return ErrorCode.CONFIGURATION_PLAN_STALE
        return None

    async def _issuers(self, plan: Plan) -> tuple[dict[str, str], tuple[Refusal, ...]]:
        """Fetch the discovery documents of the providers a plan creates or moves."""
        issuers: dict[str, str] = {}
        refusals: list[Refusal] = []
        for change in plan.changes:
            if change.kind != ISSUER_KIND or change.action is ChangeAction.DELETE:
                continue
            if change.action is ChangeAction.UPDATE and not DISCOVERY_FIELDS & set(change.fields):
                continue
            if self._issuer_of is None:
                refusals.append(
                    Refusal(change.path, ErrorCode.INTEGRATION_NOT_READY, "discovery_url")
                )
                continue
            try:
                issuers["/".join(change.address)] = await self._issuer_of(
                    str(change.values["discovery_url"]), str(change.values["client_auth"])
                )
            except DomainError as error:
                refusals.append(Refusal(change.path, error.code, error.field))
        return issuers, (*plan.refusals, *refusals) if refusals else ()

    async def _refused(self, actor: Actor, document: Document, plan: Plan) -> ApplyResult:
        """Audit a refused apply and return its plan."""
        await self._record_refusal(actor, document, plan.refusals[0].code)
        return ApplyResult(plan, None)

    async def _record_refusal(self, actor: Actor, document: Document, code: ErrorCode) -> None:
        """Audit that a document was not applied, with the reason code."""
        await self._admin.record(
            actor.subject,
            _route_permission(document.scope).value,
            Outcome.DENIED,
            code.value,
            document.scope.organisation,
            None,
            APPLY_DETAIL,
        )

    def _event(self, actor: Actor, plan: Plan, change: Change) -> AuditEvent:
        """Return the audit event of one applied change: its key and action, never a value."""
        permission = min(change.permissions, default=Permission.SETTINGS_PLATFORM_MANAGE)
        name = "/".join(change.address)
        if change.kind in PERSONAL_KINDS:
            name = sha256(name.encode()).hexdigest()[:12]
        detail = (
            f"{APPLY_DETAIL},plan:{plan.digest[:12]},action:{change.action.value},"
            f"kind:{change.kind},object:{name}"
        )
        return AuditEvent(
            self._clock.now(),
            actor.subject,
            permission.value,
            Outcome.SUCCESS,
            "",
            change.organisation,
            change.group,
            detail[:255],
        )


def _route_permission(scope: Scope) -> Permission:
    """Return the permission an export or apply of ``scope`` is audited under (point 11)."""
    return (
        Permission.SETTINGS_PLATFORM_MANAGE
        if scope.is_platform
        else Permission.SETTINGS_ORGANISATION_MANAGE
    )


def _confined(root: Entity, scope: Scope) -> Entity:
    """Return the part of the configuration a document of ``scope`` may hold."""
    if scope.is_platform:
        return root
    slug = scope.organisation
    organisations = tuple(o for o in root.children.get("organisations", ()) if o.key == (slug,))
    clients = tuple(
        c for c in root.children.get("api_clients", ()) if c.fields.get("organisation") == slug
    )
    children = {"organisations": organisations, "api_clients": clients}
    return replace(root, children={name: members for name, members in children.items() if members})


def _enriched(changes: tuple[Change, ...], issuers: Mapping[str, str]) -> tuple[Change, ...]:
    """Put the fetched issuers into the values of the provider changes that need one."""
    out: list[Change] = []
    for change in changes:
        issuer = issuers.get("/".join(change.address)) if change.kind == ISSUER_KIND else None
        out.append(
            replace(change, values={**change.values, "issuer": issuer}) if issuer else change
        )
    return tuple(out)
