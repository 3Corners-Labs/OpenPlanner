"""ORM tables. Importing this module registers every table on ``Base.metadata``.

Rows are persistence shapes only; ``app.adapters.db.uow`` maps them to domain objects.
PII columns exist only on ``bookings`` (CLAUDE.md privacy rules).

    audit_events (no foreign keys: events outlive what they describe)
    booking_events, call_status (per booking; no personal data, kept after the purge)
    integration_credentials (encrypted tokens, ADR 0008)
    admin_profiles (name per identity-provider subject; joined into the audit log)
    organisations 1──1 organisation_logos (branding, WP3b)
    organisations 1──* groups 1──* schedule_rules
                         │  └───* schedule_overrides
                         │  └───* reminder_rules
                         └──* slots 1──* bookings 1──* jobs
"""

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db.base import Base
from app.adapters.db.types import UTCDateTime

__all__ = [
    "AdminProfileRow",
    "IntegrationCredentialRow",
    "BookingEventRow",
    "CallStatusRow",
    "AuditEventRow",
    "Base",
    "BookingRow",
    "GroupRow",
    "JobRow",
    "OrganisationLogoRow",
    "OrganisationRow",
    "ReminderRuleRow",
    "ScheduleOverrideRow",
    "ScheduleRuleRow",
    "SlotRow",
]


class OrganisationRow(Base):
    """A client organisation."""

    __tablename__ = "organisations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    default_locale: Mapped[str] = mapped_column(String(16), server_default=text("'nl'"))
    display_name: Mapped[str | None] = mapped_column(String(200))
    accent_color: Mapped[str] = mapped_column(String(7), server_default=text("'#01689B'"))
    logo_media_type: Mapped[str | None] = mapped_column(String(32))
    logo_digest: Mapped[str | None] = mapped_column(String(16))


class OrganisationLogoRow(Base):
    """The logo image of an organisation; its type and digest live on ``organisations``."""

    __tablename__ = "organisation_logos"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary)


class GroupRow(Base):
    """A bookable team mapped to a WxCC queue."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    timezone: Mapped[str] = mapped_column(String(64))
    slot_minutes: Mapped[int] = mapped_column(Integer)
    min_notice_minutes: Mapped[int] = mapped_column(Integer)
    horizon_days: Mapped[int] = mapped_column(Integer)
    phone_mode: Mapped[str] = mapped_column(String(16))
    video_host_email: Mapped[str | None] = mapped_column(String(254))
    wxcc_business_address: Mapped[str | None] = mapped_column(String(255))
    wxcc_channel: Mapped[str | None] = mapped_column(String(255))
    wxcc_routing_value: Mapped[str | None] = mapped_column(String(512))
    direct_call_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    not_picked_up_minutes: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    dispatch_lead_minutes: Mapped[int] = mapped_column(Integer, server_default=text("5"))

    __table_args__ = (
        CheckConstraint("phone_mode IN ('hidden', 'optional', 'required')", name="phone_mode"),
        CheckConstraint("dispatch_lead_minutes BETWEEN 0 AND 60", name="dispatch_lead"),
    )


class ScheduleRuleRow(Base):
    """A weekly opening window of a group."""

    __tablename__ = "schedule_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    capacity: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("opens < closes", name="window"),
        CheckConstraint("capacity >= 1", name="capacity"),
    )


class ScheduleOverrideRow(Base):
    """One date's opening window, or a closed day when all window columns are NULL."""

    __tablename__ = "schedule_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    opens: Mapped[time | None] = mapped_column(Time)
    closes: Mapped[time | None] = mapped_column(Time)
    capacity: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_schedule_overrides_group_day", "group_id", "day"),
        CheckConstraint(
            "(opens IS NULL AND closes IS NULL AND capacity IS NULL) "
            "OR (opens < closes AND capacity >= 1)",
            name="window_or_closed",
        ),
    )


class ReminderRuleRow(Base):
    """When and through which channel a group reminds citizens."""

    __tablename__ = "reminder_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(16))
    minutes_before: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint("group_id", "channel", "minutes_before"),
        CheckConstraint("channel IN ('email', 'sms', 'whatsapp')", name="channel"),
    )


class SlotRow(Base):
    """A start time with at least one booking; capacity is snapshotted at creation."""

    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"))
    start: Mapped[datetime] = mapped_column(UTCDateTime())
    end: Mapped[datetime] = mapped_column(UTCDateTime())
    capacity_snapshot: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), server_default=text("'scheduled'"))

    # Backstop for the write lock: one scheduled slot per group and start. Direct calls
    # (ADR 0019) each have their own slot and are not part of it.
    __table_args__ = (
        Index(
            "uq_slots_group_start_scheduled",
            "group_id",
            "start",
            unique=True,
            sqlite_where=text("kind = 'scheduled'"),
        ),
        CheckConstraint("kind IN ('scheduled', 'direct')", name="kind"),
    )


class BookingRow(Base):
    """A citizen's appointment. The only table with personal data (nullable for the purge)."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id", ondelete="RESTRICT"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(16))
    locale: Mapped[str] = mapped_column(String(16))
    consents: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    management_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    management_token_salt: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    guest_join_url: Mapped[str | None] = mapped_column(String(2048))
    host_join_url: Mapped[str | None] = mapped_column(String(2048))
    video_room_id: Mapped[str | None] = mapped_column(String(255))
    wxcc_task_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    pii_purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    rescheduled_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_bookings_slot_status", "slot_id", "status"),
        Index("ix_bookings_video_room_id", "video_room_id"),
        Index("ix_bookings_wxcc_task_id", "wxcc_task_id"),
        CheckConstraint(
            "status IN ('confirmed', 'cancelled', 'completed', 'no_show')", name="status"
        ),
    )


class JobRow(Base):
    """A background job of a booking. Carries no personal data (CLAUDE.md)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))
    run_at: Mapped[datetime] = mapped_column(UTCDateTime())
    pinned_start: Mapped[datetime | None] = mapped_column(UTCDateTime())
    channel: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(255))
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_jobs_status_run_at", "status", "run_at"),
        Index("ix_jobs_booking_id", "booking_id"),
        CheckConstraint("status IN ('pending', 'running', 'done', 'failed')", name="status"),
    )


class AuditEventRow(Base):
    """An audited admin action (ADR 0014). Never holds personal data of citizens."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    organisation: Mapped[str | None] = mapped_column(String(64))
    group: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(String(255), server_default=text("''"))

    __table_args__ = (
        Index("ix_audit_events_at", "at"),
        Index("ix_audit_events_organisation", "organisation"),
        CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="outcome"),
    )


class AdminProfileRow(Base):
    """An admin's display name per identity-provider subject, refreshed at every login."""

    __tablename__ = "admin_profiles"

    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    last_login_at: Mapped[datetime] = mapped_column(UTCDateTime())


class BookingEventRow(Base):
    """One entry of a booking's timeline (ADR 0010); the detail is a reason code."""

    __tablename__ = "booking_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(String(128), server_default=text("''"))

    __table_args__ = (Index("ix_booking_events_booking_id", "booking_id"),)


class CallStatusRow(Base):
    """The aggregated meeting and work item state of a booking (ADR 0010)."""

    __tablename__ = "call_status"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), primary_key=True
    )
    call: Mapped[str] = mapped_column(String(16))
    task: Mapped[str] = mapped_column(String(16))
    participants: Mapped[int] = mapped_column(Integer)
    host_joined: Mapped[bool] = mapped_column(Boolean)
    host_joined_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    task_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class IntegrationCredentialRow(Base):
    """An encrypted integration secret (AES-256-GCM, ADR 0008); never readable in plain text."""

    __tablename__ = "integration_credentials"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WebhookEventRow(Base):
    """A Webex event key seen recently, so a replay is recorded once; no personal data."""

    __tablename__ = "webhook_events"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (Index("ix_webhook_events_seen_at", "seen_at"),)
