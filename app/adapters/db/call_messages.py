"""SQL queries of the chat beside a booking's call (ADR 0123 point 8, step 7b).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction (a delivery is
stored under the write lock, with the replay memory of its event). One row per message in
``call_messages``; the text is the one personal column (``privacy.CHAT_FIELDS``) and the booking's
purge deletes the rows (``delete_call_messages``). ``provider_message_id`` is unique per booking, so
a delivery that arrives twice is one row.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import CallMessageRow
from app.domain.call_chat import CallMessage, ChatAuthor, ChatState


def _message(row: CallMessageRow) -> CallMessage:
    """Return the domain message of a stored row."""
    return CallMessage(
        row.booking_id,
        ChatAuthor(row.author),
        row.message_text,
        row.at,
        ChatState(row.state),
        row.provider_message_id,
        row.id,
    )


class CallMessageQueries:
    """The chat's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def add_call_message(self, message: CallMessage) -> bool:
        """Store ``message``; ``False`` when the booking already holds that provider id.

        Must run in a locked transaction: the check and the insert are one step of it.
        """
        found = await self._session.scalar(
            select(CallMessageRow.id).where(
                CallMessageRow.booking_id == message.booking_id,
                CallMessageRow.provider_message_id == message.provider_message_id,
            )
        )
        if found is not None:
            return False
        self._session.add(
            CallMessageRow(
                booking_id=message.booking_id,
                author=message.author.value,
                message_text=message.message_text,
                at=message.at,
                state=message.state.value,
                provider_message_id=message.provider_message_id,
            )
        )
        await self._session.flush()
        return True

    async def messages_of_call(
        self, booking_id: int, since: int = 0, limit: int | None = None
    ) -> list[CallMessage]:
        """Return a booking's chat in arrival order (by id), only what is newer than ``since``.

        Arrival order and not the platform's time: a staff message carries the platform's clock and
        an own one ours, and the live page appends in arrival order, so a reload shows the same.
        """
        query = (
            select(CallMessageRow)
            .where(CallMessageRow.booking_id == booking_id, CallMessageRow.id > since)
            .order_by(CallMessageRow.id)
        )
        if limit is not None:
            query = query.limit(limit)
        rows = await self._session.scalars(query)
        return [_message(row) for row in rows]

    async def call_message_by_provider_id(
        self, booking_id: int, provider_message_id: str
    ) -> CallMessage | None:
        """Return the booking's message with that provider id, or ``None``."""
        row = await self._session.scalar(
            select(CallMessageRow).where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.provider_message_id == provider_message_id,
            )
        )
        return _message(row) if row else None

    async def participant_message_times(self, booking_id: int, after: datetime) -> list[datetime]:
        """Return the times of the participant's messages later than ``after`` (the rate window)."""
        rows = await self._session.scalars(
            select(CallMessageRow.at).where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.author == ChatAuthor.PARTICIPANT.value,
                CallMessageRow.at > after,
            )
        )
        return [at if at.tzinfo else at.replace(tzinfo=UTC) for at in rows]

    async def call_message_states(self, booking_id: int, ids: list[int]) -> dict[int, ChatState]:
        """Return the state of the participant's messages among ``ids`` (none for an empty list)."""
        if not ids:
            return {}
        rows = await self._session.execute(
            select(CallMessageRow.id, CallMessageRow.state).where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.author == ChatAuthor.PARTICIPANT.value,
                CallMessageRow.id.in_(ids),
            )
        )
        return {row_id: ChatState(state) for row_id, state in rows}

    async def has_refused_call_message(self, booking_id: int) -> bool:
        """Return whether the platform refused a message of the booking's chat."""
        found = await self._session.scalar(
            select(CallMessageRow.id)
            .where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.state == ChatState.REFUSED.value,
            )
            .limit(1)
        )
        return found is not None

    async def confirm_call_message(self, booking_id: int, provider_message_id: str) -> bool:
        """Mark the booking's ``sent`` message ``delivered``; ``False`` when there is none."""
        result = await self._session.execute(
            update(CallMessageRow)
            .where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.provider_message_id == provider_message_id,
                CallMessageRow.state == ChatState.SENT.value,
            )
            .values(state=ChatState.DELIVERED.value)
        )
        return bool(getattr(result, "rowcount", 0))

    async def refuse_sent_call_messages(self, booking_id: int) -> int:
        """Mark every ``sent`` message of the booking ``refused``; return how many.

        The platform's refusal names the task and not the message, so every message that was
        still waiting for its confirmation is the one it may mean.
        """
        result = await self._session.execute(
            update(CallMessageRow)
            .where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.state == ChatState.SENT.value,
            )
            .values(state=ChatState.REFUSED.value)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def count_call_messages(self, booking_id: int) -> int:
        """Return how many messages the booking's chat holds (see ``MAX_MESSAGES_PER_BOOKING``)."""
        count = await self._session.scalar(
            select(func.count())
            .select_from(CallMessageRow)
            .where(CallMessageRow.booking_id == booking_id)
        )
        return int(count or 0)

    async def sent_call_message_ids(self, booking_id: int) -> list[str]:
        """Return the provider ids of the booking's messages still waiting for confirmation."""
        rows = await self._session.scalars(
            select(CallMessageRow.provider_message_id).where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.state == ChatState.SENT.value,
            )
        )
        return list(rows)

    async def withdraw_call_message(self, booking_id: int, provider_message_id: str) -> bool:
        """Delete one ``sent`` message that never reached the platform; ``False`` if none.

        Only a message still waiting is deleted: one the platform confirmed or refused stays.
        """
        result = await self._session.execute(
            delete(CallMessageRow).where(
                CallMessageRow.booking_id == booking_id,
                CallMessageRow.provider_message_id == provider_message_id,
                CallMessageRow.state == ChatState.SENT.value,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def delete_call_messages(self, booking_id: int) -> None:
        """Delete the whole chat of a booking: the text goes with the booking's purge."""
        await self._session.execute(
            delete(CallMessageRow).where(CallMessageRow.booking_id == booking_id)
        )
