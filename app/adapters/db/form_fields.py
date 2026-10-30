"""SQL queries of a group's custom booking form fields (ADR 0055).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. The fields hold no personal data; the answers live in ``bookings.form_answers``
and are mapped with the booking. Texts, options and answers are stored as JSON; definitions
are validated by the domain when they are saved, and read back as they were stored.
"""

import json
from typing import Any, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow, GroupFormFieldRow, GroupRow
from app.domain.form_fields import (
    Answers,
    Classification,
    FieldDefinition,
    FieldType,
    FormDefinition,
    raise_to_pii,
)


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession


def answers_json(answers: Answers | None) -> str | None:
    """Return the JSON stored in ``bookings.form_answers`` (``None`` without answers)."""
    return json.dumps(answers.to_storage()) if answers is not None else None


def answers_from_json(body: str | None) -> Answers | None:
    """Return the answers stored by ``answers_json``."""
    return Answers.from_storage(json.loads(body)) if body else None


def _field(row: GroupFormFieldRow) -> FieldDefinition:
    """Map a field row; its definition was validated when it was saved."""
    return FieldDefinition(
        key=row.key,
        type=FieldType(row.type),
        required=row.required,
        classification=Classification(row.classification),
        options=tuple(json.loads(row.options)),
        minimum=row.minimum,
        maximum=row.maximum,
        labels=json.loads(row.labels),
        hints=json.loads(row.hints),
        option_labels=json.loads(row.option_labels),
        retired=row.retired,
    )


def _row(group_id: int, position: int, field: FieldDefinition, retired: bool) -> dict[str, Any]:
    """Return the column values of one field."""
    return {
        "group_id": group_id,
        "key": field.key,
        "position": position,
        "type": field.type.value,
        "required": field.required,
        "classification": field.classification.value,
        "options": json.dumps(list(field.options)),
        "minimum": field.minimum,
        "maximum": field.maximum,
        "labels": json.dumps(dict(field.labels)),
        "hints": json.dumps(dict(field.hints)),
        "option_labels": json.dumps({k: dict(v) for k, v in field.option_labels.items()}),
        "retired": retired,
    }


class FormFieldQueries:
    """The booking form part of ``SqlTransaction``."""

    async def get_form(self: _Transaction, group_id: int) -> FormDefinition:
        """Return the group's form: its version, active and retired fields in order."""
        version = await self._session.scalar(
            select(GroupRow.form_version).where(GroupRow.id == group_id)
        )
        rows = await self._session.scalars(
            select(GroupFormFieldRow)
            .where(GroupFormFieldRow.group_id == group_id)
            .order_by(GroupFormFieldRow.position)
        )
        fields = [_field(row) for row in rows]
        return FormDefinition(
            version or 1,
            tuple(f for f in fields if not f.retired),
            tuple(f for f in fields if f.retired),
        )

    async def save_form(self: _Transaction, group_id: int, form: FormDefinition) -> None:
        """Replace the group's fields and store the form's version."""
        await self._session.execute(
            delete(GroupFormFieldRow).where(GroupFormFieldRow.group_id == group_id)
        )
        items = [(f, False) for f in form.fields] + [(f, True) for f in form.retired]
        for position, (field, retired) in enumerate(items):
            self._session.add(GroupFormFieldRow(**_row(group_id, position, field, retired)))
        await self._session.execute(
            update(GroupRow).where(GroupRow.id == group_id).values(form_version=form.version)
        )
        await self._session.flush()

    async def raise_answers_to_pii(self: _Transaction, group_id: int, key: str) -> int:
        """Make ``key`` count as PII in every stored answer of the group; return how many."""
        rows = await self._session.execute(
            select(BookingRow.id, BookingRow.form_answers).where(
                BookingRow.group_id == group_id, BookingRow.form_answers.is_not(None)
            )
        )
        changed = 0
        for booking_id, body in rows:
            stored = json.loads(body or "{}")
            if key not in stored.get("not_pii", ()):
                continue
            await self._session.execute(
                update(BookingRow)
                .where(BookingRow.id == booking_id)
                .values(form_answers=json.dumps(raise_to_pii(stored, key)))
            )
            changed += 1
        return changed
