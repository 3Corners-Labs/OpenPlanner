"""Booking form use cases: an administrator's custom fields of a group (ADR 0055).

Every change asks ``AdminService.authorize`` for ``group.edit`` on the group first, runs in
one locked transaction, bumps the form's version and is audited with the field key and what
changed, never a label or an answer.

    view     the group's form and how many fields it may have
    add      a new field at the end; its key is never used again in the group
    update   everything but key and type
    retire   the field takes no new answers; earlier answers keep its label
    move     one place up or down

Tagging a field ``not_pii`` needs an explicit confirmation by the administrator. Raising a
field to ``pii``, or retiring it, makes every stored answer to it count as PII at once
(``Transaction.raise_answers_to_pii``), so a wrong tag cannot outlive its correction.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import Classification, FieldDefinition, FormDefinition
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scheduling import GroupSummary
from app.domain.tunable import value_of

_PERMISSION = Permission.GROUP_EDIT


@dataclass(frozen=True)
class GroupForm:
    """A group with its booking form, for the admin screen."""

    summary: GroupSummary
    form: FormDefinition
    max_custom_fields: int

    @property
    def can_add(self) -> bool:
        """Whether another field fits under the platform's limit."""
        return len(self.form.fields) < self.max_custom_fields


@dataclass(frozen=True)
class _Change:
    """The outcome of one change: the new active fields and what to audit and apply."""

    fields: tuple[FieldDefinition, ...]
    detail: str
    raise_key: str | None = None
    retired: tuple[FieldDefinition, ...] = ()


def _changes(before: FieldDefinition, after: FieldDefinition) -> list[str]:
    """Return the names of the attributes that differ, for the audit detail."""
    names = (
        "required",
        "classification",
        "options",
        "minimum",
        "maximum",
        "labels",
        "hints",
        "option_labels",
    )
    return [name for name in names if getattr(before, name) != getattr(after, name)]


class FormService:
    """Application service for the custom fields of a group's booking form.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
        max_custom_fields: The platform's limit per form (class C, ADR 0055).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        max_custom_fields: int | Callable[[], int],
    ) -> None:
        """Store the collaborators and the limit."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._max = max_custom_fields

    async def view(self, principal: Principal, slug: str) -> GroupForm:
        """Return the group's form.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        require(await self._admin.authorize(principal, _PERMISSION, group_slug=slug))
        async with self._uow.read() as tx:
            summary = await _summary(tx, slug)
            form = await tx.get_form(summary.group.id)
        return GroupForm(summary, form, value_of(self._max))

    async def add(
        self, principal: Principal, slug: str, field: FieldDefinition, *, confirmed: bool
    ) -> None:
        """Add ``field`` at the end of the form.

        Args:
            principal: The administrator.
            slug: The group.
            field: The validated definition.
            confirmed: Whether the administrator confirmed a ``not_pii`` tag.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, or ``invalid_form_field``
                naming ``fields`` (limit reached), ``key`` (used before) or
                ``confirm_not_pii``.
        """
        _check_confirmation(field, None, confirmed)

        def change(form: FormDefinition) -> _Change:
            detail = f"field.add,key:{field.key},type:{field.type}"
            return _Change(form.fields + (field,), detail)

        await self._change(principal, slug, change)

    async def update(
        self, principal: Principal, slug: str, field: FieldDefinition, *, confirmed: bool
    ) -> bool:
        """Replace the active field ``field.key``; key and type stay. Return whether it changed.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``invalid_form_field``
                naming ``key`` (no such active field), ``type`` or ``confirm_not_pii``.
        """

        def change(form: FormDefinition) -> _Change | None:
            current = _active(form, field.key)
            if field.type is not current.type:
                raise DomainError(ErrorCode.INVALID_FORM_FIELD, "type")
            _check_confirmation(field, current, confirmed)
            names = _changes(current, field)
            if not names:
                return None
            raised = (
                current.classification is Classification.NOT_PII
                and field.classification is Classification.PII
            )
            return _Change(
                tuple(field if f.key == field.key else f for f in form.fields),
                f"field.update,key:{field.key},changed:{'+'.join(names)}",
                raise_key=field.key if raised else None,
            )

        return await self._change(principal, slug, change)

    async def retire(self, principal: Principal, slug: str, key: str) -> None:
        """Retire the active field ``key``; its stored answers count as PII from now on.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``invalid_form_field``
                naming ``key``.
        """

        def change(form: FormDefinition) -> _Change:
            old = replace(_active(form, key), retired=True)
            return _Change(
                tuple(f for f in form.fields if f.key != key),
                f"field.retire,key:{key}",
                raise_key=key,
                retired=(old,),
            )

        await self._change(principal, slug, change)

    async def move(self, principal: Principal, slug: str, key: str, step: int) -> None:
        """Move the active field ``key`` one place up (``step < 0``) or down.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``invalid_form_field``
                naming ``key``.
        """

        def change(form: FormDefinition) -> _Change | None:
            _active(form, key)
            fields = list(form.fields)
            index = next(i for i, f in enumerate(fields) if f.key == key)
            target = min(max(index + (-1 if step < 0 else 1), 0), len(fields) - 1)
            if target == index:
                return None
            fields.insert(target, fields.pop(index))
            return _Change(tuple(fields), f"field.move,key:{key}")

        await self._change(principal, slug, change)

    async def _change(
        self,
        principal: Principal,
        slug: str,
        change: Callable[[FormDefinition], _Change | None],
    ) -> bool:
        """Authorise, apply ``change`` in a locked transaction, save and audit it.

        A limit lowered after fields were added keeps the existing fields editable; only
        adding one is refused. Returns whether anything changed.
        """
        require(await self._admin.authorize(principal, _PERMISSION, group_slug=slug))
        async with self._uow.locked() as tx:
            summary = await _summary(tx, slug)
            current = await tx.get_form(summary.group.id)
            result = change(current)
            if result is None:
                return False
            limit = value_of(self._max)
            if len(result.fields) <= len(current.fields):
                limit = max(limit, len(current.fields))
            form = FormDefinition.create(
                version=current.version + 1,
                fields=result.fields,
                max_custom_fields=limit,
                retired=current.retired + result.retired,
            )
            await tx.save_form(summary.group.id, form)
            if result.raise_key is not None:
                await tx.raise_answers_to_pii(summary.group.id, result.raise_key)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                _PERMISSION,
                summary.organisation_slug,
                slug,
                result.detail,
            )
        return True


async def _summary(tx: Transaction, slug: str) -> GroupSummary:
    """Return the group or raise ``group_not_found``."""
    summary = await tx.get_group_summary(slug)
    if summary is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND)
    return summary


def _active(form: FormDefinition, key: str) -> FieldDefinition:
    """Return the active field ``key`` or raise ``invalid_form_field`` naming ``key``."""
    field = form.get(key)
    if field is None:
        raise DomainError(ErrorCode.INVALID_FORM_FIELD, "key")
    return field


def _check_confirmation(
    field: FieldDefinition, current: FieldDefinition | None, confirmed: bool
) -> None:
    """Require the administrator's confirmation when a field becomes ``not_pii``."""
    lowered = field.classification is Classification.NOT_PII and (
        current is None or current.classification is Classification.PII
    )
    if lowered and not confirmed:
        raise DomainError(ErrorCode.INVALID_FORM_FIELD, "confirm_not_pii")
