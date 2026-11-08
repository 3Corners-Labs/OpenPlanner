"""Row-to-domain mapping shared by the usage queries (ADR 0117).

One home for turning stored text back into enums and an ``UsageHourRow`` back into the
``UsageCell`` the rollup wrote, so the rollup and the report reads cannot drift apart.
"""

from datetime import UTC

from app.adapters.db.models import UsageHourRow
from app.domain.booking import BookingKind
from app.domain.channels import ChannelKind
from app.domain.scheduling import DispatchKind
from app.domain.usage import ActorClass, UsageCell, UsageOutcome


def enum_of[E](kind: type[E], value: str | None) -> E | None:
    """Return the enum member stored as ``value``; ``None`` stays ``None``."""
    return kind(value) if value is not None else None  # type: ignore[call-arg]


def cell_of(row: UsageHourRow) -> UsageCell:
    """Return the cell an hourly row holds, its hour as UTC with tzinfo."""
    return UsageCell(
        bucket_hour=row.bucket_hour.replace(tzinfo=UTC)
        if row.bucket_hour.tzinfo is None
        else row.bucket_hour,
        organisation_id=row.organisation_id,
        group_id=row.group_id,
        metric=row.metric,
        count=row.count,
        sum_value=row.sum_value,
        max_value=row.max_value,
        booking_kind=enum_of(BookingKind, row.booking_kind),
        dispatch_kind=enum_of(DispatchKind, row.dispatch_kind),
        subject_code=row.subject_code,
        locale=row.locale,
        channel_kind=enum_of(ChannelKind, row.channel_kind),
        outcome=enum_of(UsageOutcome, row.outcome),
        actor_class=enum_of(ActorClass, row.actor_class),
        reason_code=row.reason_code,
        bucket=row.bucket,
    )
