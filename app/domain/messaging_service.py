"""Group messaging use cases: reminder rules and the group's own message texts (ADR 0022).

Each operation asks ``AdminService.authorize`` first and records an audit event in the same
transaction, naming what changed (``reminder.add:sms:120``, ``texts.nl:message.reminder.sms``),
never the text itself.

    reminders / add_reminder / remove_reminder   reminders.manage   group level
    texts / save_texts                           texts.manage       group level

New and removed rules apply to bookings made afterwards; planned reminders of existing
bookings are kept, so a participant never misses a reminder they were promised.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from app.domain.admin import AdminService, record_change, require
from app.domain.agent_term import TERM_KEYS, check_term_pair, clean_term
from app.domain.authorization import Permission, Principal
from app.domain.channel_settings import CONFIGURABLE_CHANNELS
from app.domain.channels import ChannelKind, ReminderRule
from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import GroupTexts, clean_text
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import Group, GroupSummary, PhoneMode

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
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        locales: tuple[str, ...],
        *,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Store the collaborators; ``on_change`` runs after a group's agent term was stored."""
        self._on_change = on_change
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
        _check_channel(group.group, rule)
        async with self._uow.locked() as tx:
            rules = await tx.get_reminder_rules(group.group.id)
            if _find(rules, rule.channel, rule.minutes_before) is not None:
                raise DomainError(ErrorCode.REMINDER_EXISTS)
            if len(rules) >= MAX_REMINDER_RULES:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "reminders")
            await tx.add_reminder_rule(group.group.id, rule)
            await self._audit(
                tx, principal, Permission.REMINDERS_MANAGE, group, _reminder_detail("add", rule)
            )

    async def reminder(
        self, principal: Principal, slug: str, channel: ChannelKind, minutes_before: int
    ) -> ReminderRule:
        """Return the group's rule with this channel and offset, to change it.

        Raises:
            DomainError: ``not_permitted`` or ``reminder_not_found``.
        """
        group = await self._group(principal, Permission.REMINDERS_MANAGE, slug)
        async with self._uow.read() as tx:
            rules = await tx.get_reminder_rules(group.group.id)
        return _existing(rules, channel, minutes_before)

    async def update_reminder(
        self,
        principal: Principal,
        slug: str,
        channel: ChannelKind,
        minutes_before: int,
        rule: ReminderRule,
    ) -> bool:
        """Change the channel or offset of a rule; ``False`` if nothing changed (ADR 0084 step 5).

        Like adding and removing, a change applies to new bookings: the reminders already
        planned for existing bookings stay, so nobody loses a reminder they were promised
        (ADR 0022 point 5).

        Raises:
            DomainError: ``not_permitted``, ``reminder_not_found``, ``reminder_exists`` (the new
                channel and offset are another rule) or ``invalid_configuration`` with
                ``channel``.
        """
        group = await self._group(principal, Permission.REMINDERS_MANAGE, slug)
        _check_channel(group.group, rule)
        async with self._uow.locked() as tx:
            rules = await tx.get_reminder_rules(group.group.id)
            current = _existing(rules, channel, minutes_before)
            rule = replace(rule, enabled=current.enabled)
            if rule == current:
                return False
            if _find(rules, rule.channel, rule.minutes_before) not in (None, current):
                raise DomainError(ErrorCode.REMINDER_EXISTS)
            await tx.update_reminder_rule(group.group.id, channel, minutes_before, rule)
            await self._audit(
                tx, principal, Permission.REMINDERS_MANAGE, group, _reminder_detail("edit", rule)
            )
        return True

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
        at all. The two words of the agent term (ADR 0077) change together. Returns the keys of
        the blocks that changed.

        Raises:
            DomainError: ``not_permitted``; ``invalid_configuration`` with ``locale`` or
                with the key of the first invalid block.
        """
        if locale not in self._locales:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "locale")
        cleaned: dict[str, str | None] = {}
        for key, text in texts.items():
            if text is None:
                cleaned[key] = None
            else:
                cleaned[key] = clean_term(key, text) if key in TERM_KEYS else clean_text(key, text)
        check_term_pair(cleaned)
        group = await self._group(principal, Permission.TEXTS_MANAGE, slug)
        async with self._uow.locked() as tx:
            current = await tx.get_group_texts(group.group.id)
            changed = [k for k, v in cleaned.items() if current.get((locale, k)) != v]
            for key in changed:
                await tx.save_group_text(group.group.id, locale, key, cleaned[key])
            if changed:
                detail = f"texts.{locale}:" + ",".join(sorted(changed))
                await self._audit(tx, principal, Permission.TEXTS_MANAGE, group, detail[:255])
        if any(key in TERM_KEYS for key in changed) and self._on_change is not None:
            self._on_change()
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


def _check_channel(group: Group, rule: ReminderRule) -> None:
    """Refuse a channel that cannot be configured, or SMS in a group without a phone field.

    Raises:
        DomainError: ``invalid_configuration`` with ``channel``.
    """
    if rule.channel not in CONFIGURABLE_CHANNELS or (
        rule.channel is ChannelKind.SMS and group.phone_mode is PhoneMode.HIDDEN
    ):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "channel")


def _find(
    rules: list[ReminderRule], channel: ChannelKind, minutes_before: int
) -> ReminderRule | None:
    """Return the rule with this channel and offset, if the group has one."""
    return next(
        (r for r in rules if (r.channel, r.minutes_before) == (channel, minutes_before)), None
    )


def _existing(rules: list[ReminderRule], channel: ChannelKind, minutes_before: int) -> ReminderRule:
    """Return the rule with this channel and offset.

    Raises:
        DomainError: ``reminder_not_found``.
    """
    found = _find(rules, channel, minutes_before)
    if found is None:
        raise DomainError(ErrorCode.REMINDER_NOT_FOUND)
    return found


def _reminder_detail(action: str, rule: ReminderRule) -> str:
    """Return ``reminder.add:sms:120``: the action, channel and offset (no personal data)."""
    return f"reminder.{action}:{rule.channel.value}:{rule.minutes_before}"
