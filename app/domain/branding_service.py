"""Branding use cases: show an organisation's branding to citizens, let admins change it.

Changing branding needs ``branding.manage`` on the organisation (superuser only, HANDOVER §6).
Every change asks ``AdminService.authorize`` first and is audited in the same transaction,
with the names of what changed, never the values. Colours are checked for contrast across
tokens before anything is stored (ADR 0041).

    public / for_group / logo / font      citizens, no authorisation (branding is public)
    update / apply_theme_file             branding.manage, organisation level
    set_logo / remove_logo                branding.manage, organisation level
    set_font / remove_font                branding.manage, organisation level
"""

from dataclasses import replace

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal, scope_for
from app.domain.branding import DEFAULT_ACCENT, Branding, FontFile, FontRole, Logo
from app.domain.errors import DomainError, ErrorCode
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, Organisation
from app.domain.theme import parse_theme_file

_EDITABLE = ("display_name", "accent_color", "theme", "header_style")


def _check_contrast(branding: Branding) -> None:
    """Raise ``invalid_configuration`` with the field of the first colour without contrast."""
    failing = branding.contrast_failure()
    if failing is not None:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, failing)


class BrandingService:
    """Application service for organisation branding.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def public(self, slug: str) -> Organisation:
        """Return the organisation with ``slug`` and its branding.

        Raises:
            DomainError: ``organisation_not_found``.
        """
        async with self._uow.read() as tx:
            return await organisation_by_slug(tx, slug)

    async def for_group(self, group: Group) -> Organisation:
        """Return the organisation a group belongs to, with its branding.

        Raises:
            DomainError: ``organisation_not_found`` (only with an inconsistent database).
        """
        async with self._uow.read() as tx:
            organisation = await tx.get_organisation_by_id(group.organisation_id)
        if organisation is None:
            raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND)
        return organisation

    async def logo(self, slug: str) -> Logo | None:
        """Return the logo of the organisation with ``slug``, if it has one."""
        async with self._uow.read() as tx:
            return await tx.get_logo(slug)

    async def font(self, slug: str, role: FontRole) -> FontFile | None:
        """Return the font of ``role`` of the organisation with ``slug``, if it has one."""
        async with self._uow.read() as tx:
            return await tx.get_font(slug, role)

    async def organisations(self, principal: Principal) -> list[Organisation]:
        """Return the organisations whose branding ``principal`` may change."""
        async with self._uow.read() as tx:
            return await tx.list_organisations(scope_for(principal, Permission.BRANDING_MANAGE))

    async def update(self, principal: Principal, slug: str, branding: Branding) -> list[str]:
        """Change name, accent, theme and header style; return the names of changed fields.

        Logo and fonts are kept; they change only through their own operations.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` with the field of a colour without enough contrast.
        """
        await self._authorize(principal, slug)
        _check_contrast(branding)
        return await self._store(principal, slug, branding, None)

    async def apply_theme_file(self, principal: Principal, slug: str, data: bytes) -> list[str]:
        """Replace accent and theme by an uploaded theme file; return the changed fields.

        Tokens the file leaves out return to their defaults, the accent included.

        Raises:
            ThemeFileError: The file is not a valid theme file (with the offending line).
            DomainError: ``not_permitted``, ``organisation_not_found`` or
                ``invalid_configuration`` with the field of a colour without enough contrast.
        """
        await self._authorize(principal, slug)
        parsed = parse_theme_file(data)
        current = (await self.public(slug)).branding
        branding = replace(
            current, accent_color=parsed.accent or DEFAULT_ACCENT, theme=parsed.theme
        )
        _check_contrast(branding)
        return await self._store(principal, slug, branding, "theme_file")

    async def _store(
        self, principal: Principal, slug: str, branding: Branding, detail: str | None
    ) -> list[str]:
        """Save the editable fields of ``branding`` if any changed, keeping logo and fonts."""
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = organisation.branding
            changed = [n for n in sorted(_EDITABLE) if getattr(current, n) != getattr(branding, n)]
            if changed:
                kept = replace(branding, logo=current.logo, fonts=current.fonts)
                await tx.save_branding(organisation.id, kept)
                await self._audit(tx, principal, slug, detail or ",".join(changed))
        return changed

    async def set_logo(self, principal: Principal, slug: str, logo: Logo) -> None:
        """Store a new logo, replacing the old one.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._save_logo(principal, slug, logo, "logo")

    async def remove_logo(self, principal: Principal, slug: str) -> None:
        """Remove the logo; pages then show the name only.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._save_logo(principal, slug, None, "logo.remove")

    async def set_font(self, principal: Principal, slug: str, font: FontFile) -> None:
        """Store a font for its role, replacing the old one.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._save_font(principal, slug, font.role, font, f"font.{font.role}")

    async def remove_font(self, principal: Principal, slug: str, role: FontRole) -> None:
        """Remove the font of ``role``; pages then use the font stack of the theme.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._save_font(principal, slug, role, None, f"font.{role}.remove")

    async def _save_font(
        self, principal: Principal, slug: str, role: FontRole, font: FontFile | None, detail: str
    ) -> None:
        """Authorise, store or remove the font of ``role``, and audit it."""
        await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            await tx.save_font(organisation.id, role, font)
            await self._audit(tx, principal, slug, detail)

    async def _save_logo(
        self, principal: Principal, slug: str, logo: Logo | None, detail: str
    ) -> None:
        """Authorise, store or remove the logo, and audit it."""
        await self._authorize(principal, slug)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            await tx.save_logo(organisation.id, logo)
            await self._audit(tx, principal, slug, detail)

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``branding.manage`` on ``slug``."""
        require(
            await self._admin.authorize(
                principal, Permission.BRANDING_MANAGE, organisation_slug=slug
            )
        )

    async def _audit(self, tx: Transaction, principal: Principal, slug: str, detail: str) -> None:
        """Record a branding change of organisation ``slug``."""
        await record_change(
            tx, self._clock.now(), principal, Permission.BRANDING_MANAGE, slug, None, detail
        )
