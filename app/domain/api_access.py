"""Use cases of the machine API (ADR 0023): manage clients, issue tokens, authorise calls.

Two kinds of caller use this service, and they never share a path:

    superuser (admin portal)  api_clients.manage, fresh login
        overview · save_settings · create_client · update_client · rotate_secret
        · set_enabled · revoke_tokens                     (every change audited)

    bot (machine API)
        issue_token(client_id, secret) ─► switches on? client enabled? secret valid and
                                          current? network allowed? scopes granted?
                                          ─► opaque token (10 min), audited
        authenticate(token)            ─► the same checks again on every call
        authorize(caller, scope, group) ─► decide_client (the matrix), denials audited

A machine caller is never more than its grant: no superuser equivalent, no route that acts as
a client, no access to personal data. Disabling a client, rotating its secret or changing its
grant revokes its tokens in the same transaction.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

from app.domain.admin import AdminService, record_change, require
from app.domain.api_clients import (
    ACCESS_TOKEN_LIFETIME,
    MAX_ACTIVE_TOKENS,
    SECRET_LIFETIME_DAYS,
    AccessToken,
    ApiAuthError,
    ApiCaller,
    ApiClient,
    ApiSettings,
    ClientDraft,
    IssuedSecret,
    IssuedToken,
    address_allowed,
    credential_hash,
    credential_matches,
    is_client_id,
    new_access_token,
    new_client_id,
    new_secret,
)
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import (
    ApiScope,
    Permission,
    Principal,
    Reason,
    Scope,
    Target,
    decide_client,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import GroupSummary, Organisation

TOKEN_EVENT = "api.token"  # noqa: S105  # nosec B105 - an audit action, not a secret
"""Audit action of a token request (success or refusal)."""

CALL_EVENT = "api.call"
"""Audit action of a refused API call."""

_MANAGE = Permission.API_CLIENTS_MANAGE
_UNKNOWN_HASH = credential_hash("")
"""Compared against when the client is unknown, so both paths cost the same."""


@dataclass(frozen=True)
class ApiOverview:
    """What the superuser's API page shows."""

    available: bool
    """Whether the deployment switched the API on (``AGENT_API_ENABLED``)."""
    settings: ApiSettings
    clients: list[ApiClient]
    organisations: list[Organisation]
    groups: list[GroupSummary]


class ApiAccessService:
    """Application service of the machine API.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises the superuser's changes and audits refusals.
        available: Whether the deployment switched the API on (``AGENT_API_ENABLED``);
            without it no token is issued or accepted, whatever the switch in the portal says.
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, admin: AdminService, *, available: bool
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._available = available

    # --- superuser ---------------------------------------------------------------------

    async def overview(self, principal: Principal) -> ApiOverview:
        """Return switches, clients, organisations and groups for the API page.

        Raises:
            DomainError: ``not_permitted``.
        """
        await self._authorize(principal)
        everything = Scope(everything=True)
        async with self._uow.read() as tx:
            return ApiOverview(
                self._available,
                await tx.get_api_settings(),
                await tx.list_api_clients(everything),
                await tx.list_organisations(everything),
                await tx.list_groups(everything),
            )

    async def settings(self) -> ApiSettings:
        """Return the switches (read by the API itself, e.g. whether docs are public)."""
        async with self._uow.read() as tx:
            return await tx.get_api_settings()

    async def save_settings(
        self, principal: Principal, *, enabled: bool, docs_public: bool
    ) -> list[str]:
        """Change the switches; return the names of those that changed.

        Raises:
            DomainError: ``not_permitted``.
        """
        await self._authorize(principal)
        async with self._uow.locked() as tx:
            current = await tx.get_api_settings()
            changed = [
                name
                for name, old, new in (
                    ("api.enabled", current.enabled, enabled),
                    ("api.docs_public", current.docs_public, docs_public),
                )
                if old != new
            ]
            if changed:
                now = self._clock.now()
                await tx.save_api_settings(ApiSettings(enabled, docs_public, now))
                await record_change(tx, now, principal, _MANAGE, None, None, ",".join(changed))
        return changed

    async def client(self, principal: Principal, client_id: str) -> ApiClient:
        """Return one client.

        Raises:
            DomainError: ``not_permitted`` or ``api_client_not_found``.
        """
        await self._authorize(principal)
        async with self._uow.read() as tx:
            return await _client(tx, client_id)

    async def create_client(
        self, principal: Principal, draft: ClientDraft, lifetime_days: int
    ) -> IssuedSecret:
        """Register a client and return it with its first secret.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` (groups outside the organisation, lifetime).
        """
        await self._authorize(principal)
        lifetime = _secret_lifetime(lifetime_days)
        secret = new_secret()
        now = self._clock.now()
        async with self._uow.locked() as tx:
            await _check_grant(tx, draft)
            client = await tx.add_api_client(
                ApiClient(
                    id=None,
                    client_id=new_client_id(),
                    name=draft.name,
                    organisation=draft.organisation,
                    groups=draft.groups,
                    scopes=draft.scopes,
                    networks=draft.networks,
                    enabled=True,
                    secret_hash=credential_hash(secret),
                    secret_expires_at=now + lifetime,
                    created_at=now,
                )
            )
            await self._audit_change(tx, principal, client, "client.create")
        return IssuedSecret(client, secret)

    async def update_client(
        self, principal: Principal, client_id: str, draft: ClientDraft
    ) -> list[str]:
        """Change name, groups, scopes and networks; the organisation never changes.

        A change to what the client may reach revokes its tokens.

        Raises:
            DomainError: ``not_permitted``, ``api_client_not_found`` or
                ``invalid_configuration``.
        """
        await self._authorize(principal)
        async with self._uow.locked() as tx:
            current = await _client(tx, client_id)
            draft = replace(draft, organisation=current.organisation)
            await _check_grant(tx, draft)
            updated = replace(
                current,
                name=draft.name,
                groups=draft.groups,
                scopes=draft.scopes,
                networks=draft.networks,
            )
            changed = [
                name
                for name in ("name", "groups", "scopes", "networks")
                if getattr(current, name) != getattr(updated, name)
            ]
            if changed:
                await tx.save_api_client(updated)
                if changed != ["name"]:
                    await tx.revoke_access_tokens(client_id)
                await self._audit_change(tx, principal, updated, ",".join(changed))
        return changed

    async def rotate_secret(
        self, principal: Principal, client_id: str, lifetime_days: int
    ) -> IssuedSecret:
        """Replace the client's secret; the old one and every token stop working at once.

        Raises:
            DomainError: ``not_permitted``, ``api_client_not_found`` or
                ``invalid_configuration`` (lifetime).
        """
        await self._authorize(principal)
        lifetime = _secret_lifetime(lifetime_days)
        secret = new_secret()
        now = self._clock.now()
        async with self._uow.locked() as tx:
            client = replace(
                await _client(tx, client_id),
                secret_hash=credential_hash(secret),
                secret_expires_at=now + lifetime,
            )
            await tx.save_api_client(client)
            await tx.revoke_access_tokens(client_id)
            await self._audit_change(tx, principal, client, "client.rotate")
        return IssuedSecret(client, secret)

    async def set_enabled(self, principal: Principal, client_id: str, enabled: bool) -> None:
        """Enable or disable a client; disabling revokes its tokens.

        Raises:
            DomainError: ``not_permitted`` or ``api_client_not_found``.
        """
        await self._authorize(principal)
        async with self._uow.locked() as tx:
            client = await _client(tx, client_id)
            if client.enabled == enabled:
                return
            client = replace(client, enabled=enabled)
            await tx.save_api_client(client)
            if not enabled:
                await tx.revoke_access_tokens(client_id)
            detail = "client.enable" if enabled else "client.disable"
            await self._audit_change(tx, principal, client, detail)

    async def revoke_tokens(self, principal: Principal, client_id: str) -> int:
        """Revoke every live token of a client; return how many there were.

        Raises:
            DomainError: ``not_permitted`` or ``api_client_not_found``.
        """
        await self._authorize(principal)
        async with self._uow.locked() as tx:
            client = await _client(tx, client_id)
            count = await tx.revoke_access_tokens(client_id)
            await self._audit_change(tx, principal, client, "client.revoke_tokens")
        return count

    # --- machines ----------------------------------------------------------------------

    async def issue_token(
        self, client_id: str, secret: str, requested: str | None, address: str | None
    ) -> IssuedToken:
        """Exchange client credentials for an access token (RFC 6749 §4.4).

        Args:
            client_id: The client's public id.
            secret: The client's secret.
            requested: Space-separated scopes asked for; ``None`` or empty means all granted.
            address: The caller's IP address, for the client's network allow-list.

        Raises:
            ApiAuthError: ``temporarily_unavailable`` when the API is switched off,
                ``invalid_client`` for any credential problem, ``invalid_scope`` for a scope
                that is unknown or not granted. Every refusal is audited.
        """
        now = self._clock.now()
        refused: ApiAuthError | None = None
        token = new_access_token()
        async with self._uow.locked() as tx:
            client = await tx.get_api_client(client_id) if is_client_id(client_id) else None
            secret_ok = credential_matches(secret, client.secret_hash if client else _UNKNOWN_HASH)
            try:
                await self._ensure_on(tx)
                if client is None:
                    raise ApiAuthError("invalid_client", "unknown_client")
                if not secret_ok:
                    raise ApiAuthError("invalid_client", "bad_secret")
                _ensure_usable(client, address)
                if now >= client.secret_expires_at:
                    raise ApiAuthError("invalid_client", "secret_expired")
                scopes = _requested_scopes(requested, client.scopes)
            except ApiAuthError as error:
                # Audited in this transaction, which commits: the refusal leaves no token.
                refused = error
                await self._audit_machine(tx, TOKEN_EVENT, client_id, client, error.reason)
            else:
                await tx.prune_access_tokens(client_id, now, keep=MAX_ACTIVE_TOKENS - 1)
                await tx.add_access_token(
                    AccessToken(
                        credential_hash(token),
                        client_id,
                        scopes,
                        now,
                        now + ACCESS_TOKEN_LIFETIME,
                    )
                )
                await tx.save_api_client(replace(client, last_token_at=now))
                await tx.add_audit_event(
                    AuditEvent(
                        now,
                        client_id,
                        TOKEN_EVENT,
                        Outcome.SUCCESS,
                        "",
                        client.organisation,
                        None,
                        ",".join(sorted(scopes)),
                    )
                )
        if refused is not None:
            raise refused
        return IssuedToken(token, int(ACCESS_TOKEN_LIFETIME.total_seconds()), scopes)

    async def authenticate(self, access_token: str, address: str | None) -> ApiCaller:
        """Return the caller behind a bearer token, checking everything again.

        Raises:
            ApiAuthError: ``temporarily_unavailable`` when switched off, ``invalid_token``
                otherwise. Refusals for a known client are audited; unknown tokens are not
                (they are guesses or noise and only logged by the caller).
        """
        now = self._clock.now()
        async with self._uow.read() as tx:
            await self._ensure_on(tx)
            stored = await tx.get_access_token(credential_hash(access_token))
            if stored is None or not credential_matches(access_token, stored.token_hash):
                raise ApiAuthError("invalid_token", "unknown_token")
            client = await tx.get_api_client(stored.client_id)
        if client is None or now >= stored.expires_at:
            raise ApiAuthError("invalid_token", "expired")
        try:
            _ensure_usable(client, address)
        except ApiAuthError as error:
            async with self._uow.locked() as tx:
                await self._audit_machine(tx, CALL_EVENT, client.client_id, client, error.reason)
            raise ApiAuthError("invalid_token", error.reason) from error
        return ApiCaller(client, stored.scopes, stored.expires_at)

    async def require_scope(self, caller: ApiCaller, api_scope: ApiScope) -> None:
        """Raise ``not_permitted`` unless the caller's token holds ``api_scope``; audit a denial.

        For calls without a group (listings); calls on one group use ``authorize``.
        """
        if api_scope in caller.grant.scopes:
            return
        await self._audit_denied_call(caller, api_scope, Reason.NOT_PERMITTED.value, None)
        raise DomainError(ErrorCode.NOT_PERMITTED)

    async def authorize(
        self, caller: ApiCaller, api_scope: ApiScope, group_slug: str
    ) -> GroupSummary:
        """Return the group if the caller may use ``api_scope`` on it; audit a denial.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted``.
        """
        async with self._uow.read() as tx:
            group = await tx.get_group_summary(group_slug)
        if group is None:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        target = Target.for_group(group.group.slug, group.organisation_slug)
        decision = decide_client(caller.grant, api_scope, target)
        if not decision.allowed:
            await self._audit_denied_call(caller, api_scope, decision.reason.value, target)
            raise DomainError(ErrorCode.NOT_PERMITTED)
        return group

    async def groups(self, caller: ApiCaller, api_scope: ApiScope) -> list[GroupSummary]:
        """Return the groups the caller may use ``api_scope`` on (none without the scope)."""
        if api_scope not in caller.grant.scopes:
            return []
        organisation = frozenset({caller.client.organisation})
        async with self._uow.read() as tx:
            found = await tx.list_groups(Scope(organisations=organisation))
        return [
            g
            for g in found
            if decide_client(
                caller.grant, api_scope, Target.for_group(g.group.slug, g.organisation_slug)
            ).allowed
        ]

    # --- helpers -----------------------------------------------------------------------

    async def _authorize(self, principal: Principal) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``api_clients.manage``."""
        require(await self._admin.authorize(principal, _MANAGE))

    async def _ensure_on(self, tx: Transaction) -> None:
        """Raise ``temporarily_unavailable`` unless the deployment and the superuser allow."""
        if not self._available or not (await tx.get_api_settings()).enabled:
            raise ApiAuthError("temporarily_unavailable", "api_disabled")

    async def _audit_change(
        self, tx: Transaction, principal: Principal, client: ApiClient, detail: str
    ) -> None:
        """Record a client change; the client id is named so the log can be followed."""
        await record_change(
            tx,
            self._clock.now(),
            principal,
            _MANAGE,
            client.organisation,
            None,
            f"{detail},{client.client_id}",
        )

    async def _audit_denied_call(
        self, caller: ApiCaller, api_scope: ApiScope, reason: str, target: Target | None
    ) -> None:
        """Record an API call the matrix refused, with the group it was aimed at."""
        async with self._uow.locked() as tx:
            await tx.add_audit_event(
                AuditEvent(
                    self._clock.now(),
                    caller.client.client_id,
                    api_scope.value,
                    Outcome.DENIED,
                    reason,
                    target.organisation if target else caller.client.organisation,
                    target.group if target else None,
                )
            )

    async def _audit_machine(
        self,
        tx: Transaction,
        action: str,
        client_id: str,
        client: ApiClient | None,
        reason: str,
    ) -> None:
        """Record a refused machine request; malformed ids are recorded as ``anonymous``."""
        actor = client_id if is_client_id(client_id) else "anonymous"
        organisation = client.organisation if client else None
        await tx.add_audit_event(
            AuditEvent(self._clock.now(), actor, action, Outcome.DENIED, reason, organisation)
        )


def _ensure_usable(client: ApiClient, address: str | None) -> None:
    """Raise ``invalid_client`` when the client is disabled or the network is not allowed."""
    if not client.enabled:
        raise ApiAuthError("invalid_client", "disabled")
    if not address_allowed(client.networks, address):
        raise ApiAuthError("invalid_client", "network")


def _requested_scopes(requested: str | None, granted: frozenset[ApiScope]) -> frozenset[ApiScope]:
    """Return the scopes asked for, or all granted ones when none are asked for.

    Raises:
        ApiAuthError: ``invalid_scope`` for a scope that is unknown or not granted.
    """
    names = (requested or "").split()
    if not names:
        return granted
    try:
        asked = frozenset(ApiScope(name) for name in names)
    except ValueError as exc:
        raise ApiAuthError("invalid_scope", "unknown_scope") from exc
    if not asked <= granted:
        raise ApiAuthError("invalid_scope", "scope_not_granted")
    return asked


def _secret_lifetime(days: int) -> timedelta:
    """Return the secret lifetime, if it is one of the offered choices.

    Raises:
        DomainError: ``invalid_configuration`` for ``lifetime_days``.
    """
    if days not in SECRET_LIFETIME_DAYS:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "lifetime_days")
    return timedelta(days=days)


async def _client(tx: Transaction, client_id: str) -> ApiClient:
    """Return the client or raise ``api_client_not_found``."""
    client = await tx.get_api_client(client_id) if is_client_id(client_id) else None
    if client is None:
        raise DomainError(ErrorCode.API_CLIENT_NOT_FOUND)
    return client


async def _check_grant(tx: Transaction, draft: ClientDraft) -> None:
    """Check that the organisation exists and every group belongs to it.

    Raises:
        DomainError: ``organisation_not_found`` or ``invalid_configuration`` for ``groups``.
    """
    if await tx.get_organisation(draft.organisation) is None:
        raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
    for slug in draft.groups:
        summary = await tx.get_group_summary(slug)
        if summary is None or summary.organisation_slug != draft.organisation:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "groups")
