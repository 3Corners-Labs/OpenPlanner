"""ORM tables. Importing this module registers every table on ``Base.metadata``.

Rows are persistence shapes only; ``app.adapters.db.uow`` maps them to domain objects.
PII columns of citizens exist only on ``bookings`` (CLAUDE.md privacy rules). ``staff`` holds
the display name of admins, from the identity provider (ADR 0046).

    audit_events (no foreign keys: events outlive what they describe)
    booking_events, call_status (per booking; no personal data, kept after the purge)
    integration_credentials (encrypted tokens, ADR 0008)
    api_settings (one row); organisations 1──* api_clients 1──* api_access_tokens
                                                 └──* api_client_groups *──1 groups (ADR 0023)
    organisations 1──* channel_settings (SMTP and SMS per organisation, secret encrypted)
    integration_reports (last webhook listing and delivery test; no secrets)
    staff 1──* staff_memberships (admins as last seen at login, ADR 0046; the name is joined
                                  into the audit log)
    release_checks (one row), update_requests (ADR 0029; the installation, no personal data)
    maintenance_state (one row, ADR 0038; the installation, no personal data)
    organisations 1──1 organisation_logos (branding, WP3b)
    organisations 1──n organisation_fonts (branding, ADR 0041)
    holiday_calendars 1──* holidays (ADR 0025; chosen through organisation_holiday_calendars
                         and group_holiday_calendars; group_holiday_open_days keep one open)
    organisations 1──* groups 1──* schedule_rules
                         │  └───* schedule_overrides
                         │  └───* reminder_rules
                         │  └───* closures 1──* closure_notices (ADR 0024)
                         │  └───* group_texts (message texts per locale and block)
                         └──* slots 1──* bookings 1──* jobs
                                         └──? closure (the one that suspended it)
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
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db.base import Base
from app.adapters.db.types import UTCDateTime

__all__ = [
    "ApiAccessTokenRow",
    "ApiClientGroupRow",
    "ApiClientRow",
    "ApiSettingsRow",
    "IntegrationCredentialRow",
    "IntegrationReportRow",
    "BookingEventRow",
    "CallStatusRow",
    "ChannelSettingsRow",
    "GroupTextRow",
    "AuditEventRow",
    "Base",
    "BookingRow",
    "GroupHolidayCalendarRow",
    "GroupHolidayOpenDayRow",
    "ClosureNoticeRow",
    "ClosureRow",
    "GroupRow",
    "HolidayCalendarRow",
    "HolidayRow",
    "JobRow",
    "OrganisationHolidayCalendarRow",
    "OrganisationLogoRow",
    "OrganisationFontRow",
    "OrganisationRow",
    "MaintenanceStateRow",
    "ReleaseCheckRow",
    "ReminderRuleRow",
    "ScheduleOverrideRow",
    "ScheduleRuleRow",
    "SlotRow",
    "StaffMembershipRow",
    "StaffRow",
    "UpdateRequestRow",
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
    theme_tokens: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    header_style: Mapped[str] = mapped_column(String(16), server_default=text("'standard'"))
    font_digests: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))

    __table_args__ = (
        CheckConstraint("header_style IN ('standard', 'accent', 'logo')", name="header_style"),
    )


class OrganisationLogoRow(Base):
    """The logo image of an organisation; its type and digest live on ``organisations``."""

    __tablename__ = "organisation_logos"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary)


class OrganisationFontRow(Base):
    """One uploaded WOFF2 font of an organisation; its digest lives on ``organisations``."""

    __tablename__ = "organisation_fonts"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)

    __table_args__ = (CheckConstraint("role IN ('text', 'text-bold', 'heading')", name="role"),)


class OrganisationContactsRow(Base):
    """The default contact block of an organisation (ADR 0037) as JSON; no personal data."""

    __tablename__ = "organisation_contacts"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    body: Mapped[str] = mapped_column(Text)


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
    direct_call_max_concurrent: Mapped[int | None] = mapped_column(Integer)
    dispatch_lead_minutes: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    # ADR 0044: a planner changes bookings up to this long before the start.
    planner_change_min_lead_minutes: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    holiday_source: Mapped[str] = mapped_column(String(16), server_default=text("'inherit'"))

    __table_args__ = (
        CheckConstraint("phone_mode IN ('hidden', 'optional', 'required')", name="phone_mode"),
        CheckConstraint("dispatch_lead_minutes BETWEEN 0 AND 60", name="dispatch_lead"),
        CheckConstraint("planner_change_min_lead_minutes BETWEEN 0 AND 1440", name="planner_lead"),
        CheckConstraint("holiday_source IN ('inherit', 'own')", name="holiday_source"),
    )


class GroupContactsRow(Base):
    """A group's own contact block (ADR 0037 §2) as JSON, overriding the organisation default."""

    __tablename__ = "group_contacts"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    body: Mapped[str] = mapped_column(Text)


class ScheduleRuleRow(Base):
    """A weekly staffing block of a group; capacity 0 is a block without agents (ADR 0027)."""

    __tablename__ = "schedule_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    capacity: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(50))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("opens < closes", name="window"),
        CheckConstraint("capacity >= 0", name="capacity"),
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
    label: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        Index("ix_schedule_overrides_group_day", "group_id", "day"),
        CheckConstraint(
            "(opens IS NULL AND closes IS NULL AND capacity IS NULL) "
            "OR (opens < closes AND capacity >= 0)",
            name="window_or_closed",
        ),
    )


class HolidayCalendarRow(Base):
    """A reusable set of closed days, maintained by the superuser (ADR 0022)."""

    __tablename__ = "holiday_calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class HolidayRow(Base):
    """One closed date of a holiday calendar."""

    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("holiday_calendars.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(200))

    __table_args__ = (UniqueConstraint("calendar_id", "day"),)


class OrganisationHolidayCalendarRow(Base):
    """A calendar an organisation chose for its groups.

    ``RESTRICT``: a calendar in use cannot disappear under an organisation.
    """

    __tablename__ = "organisation_holiday_calendars"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("holiday_calendars.id", ondelete="RESTRICT"), primary_key=True
    )


class GroupHolidayCalendarRow(Base):
    """A calendar of a group's own choice (used when ``groups.holiday_source = 'own'``)."""

    __tablename__ = "group_holiday_calendars"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("holiday_calendars.id", ondelete="RESTRICT"), primary_key=True
    )


class GroupHolidayOpenDayRow(Base):
    """A holiday on which the group is open as usual."""

    __tablename__ = "group_holiday_open_days"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)


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


class GroupTextRow(Base):
    """A group's own text of one message block in one locale (ADR 0022)."""

    __tablename__ = "group_texts"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(8), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(String(2000))


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
        # The group's recent direct calls, for the cap and the queue (ADR 0026).
        Index("ix_slots_group_kind_start", "group_id", "kind", "start"),
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
    closure_id: Mapped[int | None] = mapped_column(ForeignKey("closures.id", ondelete="SET NULL"))
    # ADR 0044: the fixed reason a planner gave; a code, never free text.
    change_reason: Mapped[str | None] = mapped_column(String(16))
    # ADR 0044: when an unanswered request to choose a new time lapses.
    rebook_by: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_bookings_slot_status", "slot_id", "status"),
        Index("ix_bookings_video_room_id", "video_room_id"),
        Index("ix_bookings_wxcc_task_id", "wxcc_task_id"),
        CheckConstraint(
            "status IN ('confirmed', 'cancelled', 'completed', 'no_show', 'suspended', "
            "'awaiting_rebook')",
            name="status",
        ),
        CheckConstraint(
            "change_reason IS NULL OR change_reason IN ('staffing', 'technical', 'unforeseen')",
            name="change_reason",
        ),
    )


class ShortfallAcceptanceRow(Base):
    """A planner accepted a slot's shortfall at these counts (ADR 0044); no personal data."""

    __tablename__ = "shortfall_acceptances"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    start: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    agents: Mapped[int] = mapped_column(Integer)
    booked: Mapped[int] = mapped_column(Integer)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    actor: Mapped[str] = mapped_column(String(255))


class ClosureRow(Base):
    """An emergency closure of a group (ADR 0024); ``ends_at`` NULL until it is lifted."""

    __tablename__ = "closures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"), index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # The JSON copy of the contact block taken when the closure started (ADR 0037 §2).
    contacts: Mapped[str | None] = mapped_column(Text)


class ClosureNoticeRow(Base):
    """The notice and optional heading of a closure, in one locale. Written by an admin; no PII."""

    __tablename__ = "closure_notices"

    closure_id: Mapped[int] = mapped_column(
        ForeignKey("closures.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(String(1000))
    heading: Mapped[str | None] = mapped_column(String(120))


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
    """An audited admin action (ADR 0014) or system event (ADR 0031).

    Never holds personal data of citizens; ``booking_id`` is a plain id without a foreign key,
    so an event outlives what it describes.
    """

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
    source: Mapped[str] = mapped_column(String(16), server_default=text("'admin'"))
    booking_id: Mapped[int | None] = mapped_column(Integer)
    # ADR 0040, part 2: the fields of the one event schema that are not derived on export.
    event_id: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), server_default=text("'info'"))
    category: Mapped[str] = mapped_column(String(16), server_default=text("'audit'"))
    trace_id: Mapped[str] = mapped_column(String(32), server_default=text("''"))
    service: Mapped[str] = mapped_column(String(16), server_default=text("''"))

    __table_args__ = (
        Index("ix_audit_events_at", "at"),
        Index("ix_audit_events_organisation", "organisation"),
        Index("ix_audit_events_group", "group"),
        Index("ix_audit_events_source_at", "source", "at"),
        Index("ix_audit_events_event_id", "event_id", unique=True),
        Index("ix_audit_events_trace_id", "trace_id"),
        CheckConstraint(
            "outcome IN ('success', 'denied', 'failure', 'retry', 'skipped')", name="outcome"
        ),
        CheckConstraint("source IN ('admin', 'system')", name="source"),
        CheckConstraint("level IN ('debug', 'info', 'warning', 'error', 'critical')", name="level"),
        CheckConstraint(
            "category IN ('audit', 'system', 'security', 'diagnostic')", name="category"
        ),
    )


class StaffRow(Base):
    """An admin as last seen at login (ADR 0046); never a source of rights.

    ``roles`` is the comma-separated role list of the last login, ``NULL`` for a record
    carried over from ``admin_profiles`` whose roles are not known yet.
    """

    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    roles: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_login_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_logout_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class StaffMembershipRow(Base):
    """An organisation, or a group with its organisation, that an admin's roles reach."""

    __tablename__ = "staff_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    organisation: Mapped[str] = mapped_column(String(64))
    group: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_staff_memberships_staff_id", "staff_id"),
        Index("ix_staff_memberships_organisation", "organisation"),
        Index("ix_staff_memberships_group", "group"),
    )


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


class ChannelSettingsRow(Base):
    """An organisation's SMTP server or SMS gateway (ADR 0022).

    ``settings`` holds the non-secret fields as JSON; the password or service key is sealed
    with AES-256-GCM under ``channel:<organisation id>:<channel>`` (ADR 0008).
    """

    __tablename__ = "channel_settings"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    settings: Mapped[str] = mapped_column(String(2000))
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (CheckConstraint("channel IN ('email', 'sms')", name="channel"),)


class IntegrationReportRow(Base):
    """The last result of an operator check (JSON), shown read-only in the admin portal."""

    __tablename__ = "integration_reports"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WebhookEventRow(Base):
    """A Webex event key seen recently, so a replay is recorded once; no personal data."""

    __tablename__ = "webhook_events"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (Index("ix_webhook_events_seen_at", "seen_at"),)


class ApiSettingsRow(Base):
    """The superuser's switches for the machine API (ADR 0023); at most one row."""

    __tablename__ = "api_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    docs_public: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class ApiClientRow(Base):
    """A registered bot (ADR 0023); only the SHA-256 of its secret is stored."""

    __tablename__ = "api_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), index=True
    )
    scopes: Mapped[str] = mapped_column(String(255))
    networks: Mapped[str] = mapped_column(String(1024), server_default=text("''"))
    enabled: Mapped[bool] = mapped_column(Boolean)
    secret_hash: Mapped[str] = mapped_column(String(64))
    secret_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_token_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ApiClientGroupRow(Base):
    """A group an API client is narrowed to; none means every group of its organisation."""

    __tablename__ = "api_client_groups"

    client_pk: Mapped[int] = mapped_column(
        ForeignKey("api_clients.id", ondelete="CASCADE"), primary_key=True
    )
    # RESTRICT: removing the group must not widen the client to the whole organisation.
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="RESTRICT"), primary_key=True
    )


class ApiAccessTokenRow(Base):
    """An issued access token: its SHA-256, scopes and lifetime (ADR 0023)."""

    __tablename__ = "api_access_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_pk: Mapped[int] = mapped_column(ForeignKey("api_clients.id", ondelete="CASCADE"))
    scopes: Mapped[str] = mapped_column(String(255))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_api_access_tokens_client_pk", "client_pk"),
        Index("ix_api_access_tokens_expires_at", "expires_at"),
    )


class ReleaseCheckRow(Base):
    """The outcome of the latest release check (ADR 0029); at most one row."""

    __tablename__ = "release_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())
    latest_version: Mapped[str | None] = mapped_column(String(20))
    release_url: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str] = mapped_column(String(40), server_default=text("''"))

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class UpdateRequestRow(Base):
    """A superuser's request to update or restore the installation (ADR 0029)."""

    __tablename__ = "update_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(16))
    target_version: Mapped[str] = mapped_column(String(20))
    from_version: Mapped[str] = mapped_column(String(20))
    requested_by: Mapped[str] = mapped_column(String(255))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(String(40), server_default=text("''"))
    step: Mapped[str | None] = mapped_column(String(16))
    step_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_update_requests_status", "status"),
        CheckConstraint("action IN ('update', 'restore')", name="action"),
        CheckConstraint(
            "status IN ('requested', 'running', 'succeeded', 'rolled_back', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "step IS NULL OR step IN ('pulling', 'verifying', 'backing_up', 'migrating', "
            "'starting', 'rolling_back')",
            name="step",
        ),
    )


class MaintenanceStateRow(Base):
    """The maintenance switch of the whole installation (ADR 0038); at most one row."""

    __tablename__ = "maintenance_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    switched_by: Mapped[str] = mapped_column(String(255), server_default=text("''"))
    expected_end: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)
