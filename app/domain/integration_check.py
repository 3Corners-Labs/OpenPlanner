"""The "Koppeling controleren" use case: check a group's integration chain on demand.

Whoever may edit a group's settings (``group.edit``) may check them; both the check and its
latest result are behind the matrix (ADR 0014). The platforms are read through the
``IntegrationChecker`` port, never written. The result replaces the previous one and every
run is audited with its overall status, so admins can see who checked and what came out.

    run(principal, slug)   authorise ─► checker.check_group(group, peers) ─► store ─► audit
    last(principal, slug)  authorise ─► latest stored result (or None)
"""

from app.domain.admin import AdminService, require
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Principal, Scope
from app.domain.checks import CheckArea, CheckStatus, Finding, GroupCheck
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, IntegrationChecker, Transaction, UnitOfWork
from app.domain.scheduling import Group

CHECK_EVENT = "integration.check"
"""Audit action of a check; the reason is the overall status."""

NOT_CONFIGURED = Finding(CheckArea.PLATFORM, "platform.not_configured", CheckStatus.SKIPPED)


class IntegrationCheckService:
    """Application service for checking a group's Webex, WxCC and webhook configuration.

    Args:
        uow: Opens database transactions.
        clock: Current time.
        admin: Authorises and audits.
        checker: Reads the platforms; ``None`` when no Service App is configured.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        checker: IntegrationChecker | None,
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._checker = checker

    async def run(self, principal: Principal, slug: str) -> GroupCheck:
        """Check the group now and keep the result.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        organisation = await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await _group(tx, slug)
            peers = [
                summary.group
                for summary in await tx.list_groups(Scope(organisations=frozenset({organisation})))
            ]
        findings = (
            await self._checker.check_group(group, peers) if self._checker else [NOT_CONFIGURED]
        )
        check = GroupCheck(group.id, self._clock.now(), tuple(findings))
        async with self._uow.locked() as tx:
            await tx.save_group_check(check)
        outcome = Outcome.FAILURE if check.overall is CheckStatus.FAIL else Outcome.SUCCESS
        await self._admin.record(
            principal.subject, CHECK_EVENT, outcome, check.overall.value, organisation, slug
        )
        return check

    async def last(self, principal: Principal, slug: str) -> GroupCheck | None:
        """Return the latest check of the group, if it was ever checked.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            group = await _group(tx, slug)
            return await tx.get_group_check(group.id)

    async def _authorize(self, principal: Principal, slug: str) -> str:
        """Require ``group.edit`` on the group; return its organisation slug."""
        authorization = require(
            await self._admin.authorize(principal, Permission.GROUP_EDIT, group_slug=slug)
        )
        organisation = authorization.target.organisation
        if organisation is None:  # pragma: no cover - a group target always has one
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return organisation


async def _group(tx: Transaction, slug: str) -> Group:
    """Return the group with ``slug``.

    Raises:
        DomainError: ``group_not_found``.
    """
    group = await tx.get_group(slug)
    if group is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return group
