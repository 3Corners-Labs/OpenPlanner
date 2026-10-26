"""Group messaging use cases: reminder rules and the group's own message texts (ADR 0022).

Each operation asks ``AdminService.authorize`` first and records an audit event in the same
transaction, naming what changed (``reminder.add:sms:120``, ``texts.nl:message.reminder.sms``),
never the text itself.

    reminders / add_reminder / remove_reminder   reminders.manage   group level
    texts / save_texts                           texts.manage       group level

New and removed rules apply to bookings made afterwards; planned reminders of existing
bookings are kept, so a citizen never misses a reminder they were promised.
"""

from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.channel_settings import CONFIGURABLE_CHANNELS
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import GroupTexts, clean_text
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import GroupSummary, PhoneMode

MAX_REMINDER_RULES = 5
"""Per group; more reminders than this would read as spam."""


@dataclass(frozen=True)
class Reminders:
    """A group's reminder rules and whether its organisation can deliver them.

    Attributes:
        rules: Sorted by channel, then by offset (earliest reminder first).
        ready: Channels the organisation has configured with their secret.
        asks_phone: Whether the group's booking form asks for a phone number (SMS needs one).
    """

    rules: list[ReminderRule]
    ready: frozenset[ChannelKind]
    asks_phone: bool


class MessagingService:
    """Application service for a group's reminders and message texts.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
        locales: Locales whose texts can be edited (the catalogue's, default first).
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, admin: AdminService, locales: tuple[str, ...]
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._locales = locales

    async def reminders(self, principal: Principal, slug: str) -> Reminders:
        """Return the group's reminder rules.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted``.
        """
        group = await self._group(principal, Permission.REMINDERS_MANAGE, slug)
        async with self._uow.read() as tx:
            rules = await tx.get_reminder_rules(group.group.id)
            settings = await tx.get_channel_settings(group.group.organisation_id)
        return Reminders(
            sorted(rules, key=lambda r: (r.channel.value, -r.minutes_before)),
            frozenset(s.channel for s in settings if s.ready),
            group.group.phone_mode is not PhoneMode.HIDDEN,
        )

    async def add_reminder(self, principal: Principal, slug: str, rule: ReminderRule) -> None:
        """Add a reminder rule to the group.

        Raises:
            DomainError: ``not_permitted``; ``invalid_configuration`` with ``channel`` for a
                channel that cannot be configured (WhatsApp) or SMS without a phone field, or
                with ``reminders`` above ``MAX_REMINDER_RULES``; ``reminder_exists``.
        """
        group = await self._group(principal, Permission.REMINDERS_MANAGE, slug)
        if rule.channel not in CONFIGURABLE_CHANNELS or (
            rule.channel is ChannelKind.SMS and group.group.phone_mode is PhoneMode.HIDDEN
        ):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "channel")
        async with self._uow.locked() as tx:
            rules = await tx.get_reminder_rules(group.group.id)
            if any(
                (r.channel, r.minutes_before) == (rule.channel, rule.minutes_before) for r in rules
            ):
                raise DomainError(ErrorCode.REMINDER_EXISTS)
            if len(rules) >= MAX_REMINDER_RULES:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "reminders")
            await tx.add_reminder_rule(group.group.id, rule)
            await self._audit(
                tx, principal, Permission.REMINDERS_MANAGE, group, _reminder_detail("add", rule)
            )

    async def remove_reminder(
        self, principal: Principal, slug: str, channel: ChannelKind, minutes_before: int
    ) -> None:
        """Remove a reminder rule from the group.

        Raises:
            DomainError: ``not_permitted`` or ``reminder_not_found``.
        """
        group = await self._group(principal, Permission.REMINDERS_MANAGE, slug)
        async with self._uow.locked() as tx:
            if not await tx.delete_reminder_rule(group.group.id, channel, minutes_before):
                raise DomainError(ErrorCode.REMINDER_NOT_FOUND)
            detail = _reminder_detail("remove", ReminderRule(channel, minutes_before))
            await self._audit(tx, principal, Permission.REMINDERS_MANAGE, group, detail)

    async def texts(self, principal: Principal, slug: str) -> GroupTexts:
        """Return the group's own texts by ``(locale, block key)``.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted``.
        """
        group = await self._group(principal, Permission.TEXTS_MANAGE, slug)
        async with self._uow.read() as tx:
            return await tx.get_group_texts(group.group.id)

    async def save_texts(
        self, principal: Principal, slug: str, locale: str, texts: dict[str, str | None]
    ) -> list[str]:
        """Store the group's texts of one locale; ``None`` brings a block's default back.

        Every text is validated before anything is stored, so a form is saved whole or not
        at all. Returns the keys of the blocks that changed.

        Raises:
            DomainError: ``not_permitted``; ``invalid_configuration`` with ``locale`` or
                with the key of the first invalid block.
        """
        if locale not in self._locales:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "locale")
        cleaned = {
            key: clean_text(key, text) if text is not None else None for key, text in texts.items()
        }
        group = await self._group(principal, Permission.TEXTS_MANAGE, slug)
        async with self._uow.locked() as tx:
            current = await tx.get_group_texts(group.group.id)
            changed = [k for k, v in cleaned.items() if current.get((locale, k)) != v]
            for key in changed:
                await tx.save_group_text(group.group.id, locale, key, cleaned[key])
            if changed:
                detail = f"texts.{locale}:" + ",".join(sorted(changed))
                await self._audit(tx, principal, Permission.TEXTS_MANAGE, group, detail[:255])
        return changed

    async def _group(self, principal: Principal, permission: Permission, slug: str) -> GroupSummary:
        """Authorise ``permission`` on the group and return it.

        Raises:
            DomainError: ``group_not_found`` or ``not_permitted``.
        """
        authorization = require(await self._admin.authorize(principal, permission, group_slug=slug))
        if authorization.group is None:  # pragma: no cover - group-level always has one
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return authorization.group

    async def _audit(
        self,
        tx: Transaction,
        principal: Principal,
        permission: Permission,
        group: GroupSummary,
        detail: str,
    ) -> None:
        """Record a messaging change of ``group``."""
        await record_change(
            tx,
            self._clock.now(),
            principal,
            permission,
            group.organisation_slug,
            group.group.slug,
            detail,
        )


def _reminder_detail(action: str, rule: ReminderRule) -> str:
    """Return ``reminder.add:sms:120``: the action, channel and offset (no personal data)."""
    return f"reminder.{action}:{rule.channel.value}:{rule.minutes_before}"
