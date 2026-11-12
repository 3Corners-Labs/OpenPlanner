"""ORM tables. Importing this module registers every table on ``Base.metadata``.

Rows are persistence shapes only; ``app.adapters.db.uow`` maps them to domain objects.
PII columns of participants exist only on ``bookings`` (CLAUDE.md privacy rules). ``staff`` holds
the display name of admins, from the identity provider (ADR 0046).

    audit_events (no foreign keys: events outlive what they describe)
    booking_events, call_status (per booking; no personal data, kept after the purge)
    integration_credentials (encrypted tokens, ADR 0008)
    api_settings (one row); organisations 1──* api_clients 1──* api_access_tokens
                                                 └──* api_client_groups *──1 groups (ADR 0023)
    organisations 1──* channel_settings (SMTP and SMS per organisation, secret encrypted)
    organisations 1──* identity_providers 1──* identity_provider_keys (ADR 0083; the secret and
                                               the private key encrypted)
    integration_reports (last webhook listing and delivery test; no secrets)
    staff 1──* staff_memberships (admins as last seen at login, ADR 0046; the name is joined
                                  into the audit log)
    admin_profiles (name per identity-provider subject; joined into the audit log)
    roles (custom roles of the superuser, ADR 0039; no foreign keys, linked by claim value)
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
                         │            └──? closure_images (ADR 0037 §3; one per closure)
    organisations 1──* closure_scenarios ──? closure_images (ADR 0037 §4; group_id NULL: the
                         organisation's, else one group's)
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
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db.base import Base
from app.adapters.db.types import UTCDateTime

__all__ = [
    "ApiAccessTokenRow",
    "DiagnosticModeRow",
    "ApiClientGroupRow",
    "ApiClientRow",
    "ApiSettingsRow",
    "IntegrationCredentialRow",
    "IntegrationReportRow",
    "BookingEventRow",
    "CallStatusRow",
    "UsageBackfillRow",
    "UsageEventRow",
    "UsageHourRow",
    "UsageRollupStateRow",
    "UsageStaffHourRow",
    "ChannelSettingsRow",
    "GroupTextRow",
    "OrganisationTextRow",
    "AuditEventRow",
    "Base",
    "BookingRow",
    "GroupHolidayCalendarRow",
    "GroupHolidayOpenDayRow",
    "ClosureImageRow",
    "ClosureNoticeRow",
    "ClosureScenarioRow",
    "ClosureRow",
    "GroupRow",
    "HolidayCalendarRow",
    "HolidayRow",
    "IdentityProviderKeyRow",
    "IdentityProviderRow",
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
    "StaffRefusalRow",
    "StaffRow",
    "UpdateRequestRow",
]


def _slug_as_idp_key(context: DefaultExecutionContext) -> str:
    """The IdP value of a new organisation or group starts as its slug (ADR 0049 point 4)."""
    parameters: dict[str, object] = context.get_current_parameters()  # type: ignore[no-untyped-call]
    return str(parameters["slug"])


class OrganisationRow(Base):
    """A client organisation."""

    __tablename__ = "organisations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    # ADR 0049: what the identity provider names the organisation by; the slug at creation.
    idp_key: Mapped[str] = mapped_column(String(64), unique=True, default=_slug_as_idp_key)
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


class OrganisationOriginRow(Base):
    """A website allowed to embed the organisation's groups (ADR 0035); no personal data."""

    __tablename__ = "organisation_origins"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    origin: Mapped[str] = mapped_column(String(200), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)


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
    # Before ADR 0032: the WxCC routing. Written alongside ``group_contact_center`` and, for
    # WxCC, the source of truth on read this minor (ADR 0116 §5, contact_center_rows.py).
    wxcc_business_address: Mapped[str | None] = mapped_column(String(255))
    wxcc_channel: Mapped[str | None] = mapped_column(String(255))
    wxcc_routing_value: Mapped[str | None] = mapped_column(String(512))
    not_picked_up_minutes: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    direct_call_max_concurrent: Mapped[int | None] = mapped_column(Integer)
    dispatch_lead_minutes: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    dispatch_kind: Mapped[str] = mapped_column(String(32), server_default=text("'contact_center'"))
    # ADR 0069: the participant moves a booking up to this long before the start.
    participant_change_lead_minutes: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    # ADR 0044: a planner changes bookings up to this long before the start.
    planner_change_min_lead_minutes: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    # ADR 0071: a trial call on Webex ahead of the appointment.
    trial_call_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    trial_call_minutes: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    trial_calls_per_booking: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    # ADR 0075: which Webex page the participant's join link opens.
    join_client: Mapped[str] = mapped_column(String(16), server_default=text("'browser'"))
    embedding_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    queue_visible_from_minutes: Mapped[int] = mapped_column(Integer, server_default=text("15"))
    queue_free_pick_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    # ADR 0066: future bookings one email address may hold in this group.
    max_bookings_per_email: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    # ADR 0057 point 6: the rebooking assistant may send its proposals without a review.
    reschedule_auto_send: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    reschedule_auto_delay_minutes: Mapped[int] = mapped_column(Integer, server_default=text("15"))
    reschedule_auto_max_bookings: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    calendar_invite_address: Mapped[str | None] = mapped_column(String(254))
    # ADR 0082 point 11: the fallback to the own queue, the team's space, and its state.
    dispatch_fallback: Mapped[str] = mapped_column(String(16), server_default=text("'none'"))
    fallback_space_room_id: Mapped[str | None] = mapped_column(String(200))
    fallback_active_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    holiday_source: Mapped[str] = mapped_column(String(16), server_default=text("'inherit'"))
    form_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    # ADR 0049: what the identity provider names the group by, unique in its organisation.
    idp_key: Mapped[str] = mapped_column(String(64), default=_slug_as_idp_key)
    # ADR 0084: set when the group is archived; NULL while it is in use.
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # ADR 0081: subjects, switched-on routing attributes and their mapping, as JSON; no PII.
    routing: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("organisation_id", "idp_key", name="uq_groups_organisation_idp_key"),
        CheckConstraint("phone_mode IN ('hidden', 'optional', 'required')", name="phone_mode"),
        CheckConstraint("dispatch_lead_minutes BETWEEN 0 AND 60", name="dispatch_lead"),
        CheckConstraint(
            "dispatch_kind IN ('contact_center', 'openplanner_queue')", name="dispatch_kind"
        ),
        CheckConstraint(
            "participant_change_lead_minutes BETWEEN 0 AND 1440", name="participant_lead"
        ),
        CheckConstraint("planner_change_min_lead_minutes BETWEEN 0 AND 1440", name="planner_lead"),
        CheckConstraint("trial_call_minutes BETWEEN 10 AND 60", name="trial_call_minutes"),
        CheckConstraint("trial_calls_per_booking BETWEEN 1 AND 5", name="trial_calls_per_booking"),
        CheckConstraint("join_client IN ('browser', 'choice')", name="join_client"),
        CheckConstraint("queue_visible_from_minutes BETWEEN 0 AND 120", name="queue_visible_from"),
        CheckConstraint("holiday_source IN ('inherit', 'own')", name="holiday_source"),
        CheckConstraint("max_bookings_per_email BETWEEN 1 AND 10", name="max_bookings_per_email"),
        CheckConstraint(
            "reschedule_auto_delay_minutes BETWEEN 5 AND 120", name="reschedule_auto_delay"
        ),
        CheckConstraint(
            "reschedule_auto_max_bookings BETWEEN 1 AND 100", name="reschedule_auto_max"
        ),
    )


class GroupFormFieldRow(Base):
    """A custom field of a group's booking form (ADR 0055); no personal data.

    Retired fields stay, so their labels explain earlier answers and their keys are not used
    again. Texts are JSON objects of locale to text; options a JSON list of keys.
    """

    __tablename__ = "group_form_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16))
    required: Mapped[bool] = mapped_column(Boolean)
    classification: Mapped[str] = mapped_column(String(8))
    options: Mapped[str] = mapped_column(Text, server_default=text("'[]'"))
    minimum: Mapped[int | None] = mapped_column(Integer)
    maximum: Mapped[int | None] = mapped_column(Integer)
    labels: Mapped[str] = mapped_column(Text)
    hints: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    option_labels: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    retired: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("group_id", "key"),
        CheckConstraint("classification IN ('pii', 'not_pii')", name="classification"),
    )


class GroupContactCenterRow(Base):
    """A group's routing at its contact-center platform (ADR 0032 point 3); no personal data.

    ``attributes`` is a JSON object of strings, validated against the platform's descriptor
    when it is read (``app.adapters.contact_centers``).
    """

    __tablename__ = "group_contact_center"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    routing_value: Mapped[str | None] = mapped_column(String(512))
    attributes: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))

    __table_args__ = (
        CheckConstraint(
            "json_valid(attributes) AND json_type(attributes) = 'object'", name="attributes"
        ),
    )


class GroupContactsRow(Base):
    """A group's own contact block (ADR 0037 §2) as JSON, overriding the organisation default."""

    __tablename__ = "group_contacts"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    body: Mapped[str] = mapped_column(Text)


OFFERS = "('scheduled', 'direct', 'both')"
"""What a staffing block offers (ADR 0072), as the database checks it."""


class ScheduleRuleRow(Base):
    """A weekly staffing block of a group; capacity 0 is a block without agents (ADR 0027).

    ``offer`` says whether the block offers slots, direct calls or both (ADR 0072).
    """

    __tablename__ = "schedule_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    capacity: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(50))
    offer: Mapped[str] = mapped_column(String(9), server_default=text("'scheduled'"))
    # ADR 0081 point 4: the subject codes the block offers, comma-separated; NULL offers all.
    subjects: Mapped[str | None] = mapped_column(String(1000))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("opens < closes", name="window"),
        CheckConstraint("capacity >= 0", name="capacity"),
        CheckConstraint(f"offer IN {OFFERS}", name="offer"),
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
    offer: Mapped[str] = mapped_column(String(9), server_default=text("'scheduled'"))
    # ADR 0081 point 4: the subject codes the block offers, comma-separated; NULL offers all.
    subjects: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        Index("ix_schedule_overrides_group_day", "group_id", "day"),
        CheckConstraint(
            "(opens IS NULL AND closes IS NULL AND capacity IS NULL) "
            "OR (opens < closes AND capacity >= 0)",
            name="window_or_closed",
        ),
        CheckConstraint(f"offer IN {OFFERS}", name="offer"),
    )


class PoolStaffingRuleRow(Base):
    """A weekly staffing block of a counted pool; ``units`` is the number of people counted.

    Shaped like ``ScheduleRuleRow`` without subjects (ADR 0122 point 1). ``offer`` says whether
    the block takes appointments (``scheduled``), work without appointment (``direct``) or both.
    """

    __tablename__ = "pool_staffing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("capacity_pools.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(Integer)
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    units: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(50))
    offer: Mapped[str] = mapped_column(String(9), server_default=text("'scheduled'"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("opens < closes", name="window"),
        CheckConstraint("units >= 0", name="units"),
        CheckConstraint(f"offer IN {OFFERS}", name="offer"),
    )


class PoolStaffingOverrideRow(Base):
    """One date's staffing window of a counted pool, or a closed day when the window is NULL."""

    __tablename__ = "pool_staffing_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("capacity_pools.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    opens: Mapped[time | None] = mapped_column(Time)
    closes: Mapped[time | None] = mapped_column(Time)
    units: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(50))
    offer: Mapped[str] = mapped_column(String(9), server_default=text("'scheduled'"))

    __table_args__ = (
        Index("ix_pool_staffing_overrides_pool_id_day", "pool_id", "day"),
        CheckConstraint(
            "(opens IS NULL AND closes IS NULL AND units IS NULL) "
            "OR (opens < closes AND units >= 0)",
            name="window_or_closed",
        ),
        CheckConstraint(f"offer IN {OFFERS}", name="offer"),
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
    """When and through which channel a group reminds participants."""

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


class OrganisationTextRow(Base):
    """An organisation's own text of one catalogue key in one locale (ADR 0004, 0074)."""

    __tablename__ = "organisation_texts"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
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
    # ADR 0122 step 2: NULL for every legacy slot; the pool a slot of a non-legacy flow draws on,
    # and the end of its appointment plus the wrap-up, fixed at creation.
    capacity_pool_id: Mapped[int | None] = mapped_column(
        ForeignKey("capacity_pools.id", ondelete="RESTRICT")
    )
    occupied_until: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Backstop for the write lock: one legacy scheduled slot per group and start. Direct calls
    # (ADR 0019) each have their own slot and are not part of it; neither is a pool slot, which
    # is unique per pool, service, start and length instead (ADR 0122).
    __table_args__ = (
        Index(
            "uq_slots_group_start_scheduled",
            "group_id",
            "start",
            unique=True,
            sqlite_where=text("kind = 'scheduled' AND capacity_pool_id IS NULL"),
        ),
        Index(
            "uq_slots_pool",
            "capacity_pool_id",
            "group_id",
            "start",
            "occupied_until",
            unique=True,
            sqlite_where=text("capacity_pool_id IS NOT NULL"),
        ),
        # The commitments of a pool in a window (ADR 0122).
        Index("ix_slots_pool_start", "capacity_pool_id", "start"),
        # The group's recent direct calls, for the cap and the queue (ADR 0026).
        Index("ix_slots_group_kind_start", "group_id", "kind", "start"),
        CheckConstraint("kind IN ('scheduled', 'direct')", name="kind"),
    )


class BookingRow(Base):
    """A participant's appointment. The only table with personal data (nullable for the purge)."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id", ondelete="RESTRICT"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(16))
    locale: Mapped[str] = mapped_column(String(16))
    consents: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    management_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    management_token_salt: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    # ADR 0093: names the booking in a URL; not PII, authorises nothing, survives the purge.
    public_ref: Mapped[str] = mapped_column(String(32), unique=True)
    guest_join_url: Mapped[str | None] = mapped_column(String(2048))
    host_join_url: Mapped[str | None] = mapped_column(String(2048))
    video_room_id: Mapped[str | None] = mapped_column(String(255))
    # ADR 0032: the task at the contact center and the platform that holds it; a provider
    # link, purged with the contact (privacy.DERIVED_FIELDS).
    contact_center_task_id: Mapped[str | None] = mapped_column(String(255))
    contact_center_kind: Mapped[str | None] = mapped_column(String(32))
    # Before ADR 0032; written for a Webex Contact Center task and, for that platform, the
    # source of truth on read this minor (ADR 0116 §5, contact_center_rows.py).
    wxcc_task_id: Mapped[str | None] = mapped_column(String(255))
    # ADR 0071: the trial call on Webex; the link is PII and purged with the contact.
    trial_room_id: Mapped[str | None] = mapped_column(String(255))
    trial_join_url: Mapped[str | None] = mapped_column(String(2048))
    trial_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    trial_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
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
    rebook_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    # ADR 0056: the deadline to confirm by email link, and when the participant did.
    confirm_by: Mapped[datetime | None] = mapped_column(UTCDateTime())
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # ADR 0055: answers to the group's custom fields as JSON; PII, purged as a whole.
    form_answers: Mapped[str | None] = mapped_column(Text)
    # ADR 0081: the chosen subject's code and the attribute codes (JSON); no PII, purged with
    # the booking all the same (privacy.ROUTING_FIELDS).
    subject_code: Mapped[str | None] = mapped_column(String(40))
    routing_answers: Mapped[str | None] = mapped_column(String(500))
    # ADR 0121: the flow revision the booking was made under; NULL is the group's default flow
    # for its entry path (a legacy booking). No foreign key: a column pair added to the large
    # table stays a plain ALTER, and the adapter writes only revisions that exist.
    flow_id: Mapped[int | None] = mapped_column(Integer)
    flow_revision: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_bookings_slot_status", "slot_id", "status"),
        Index("ix_bookings_video_room_id", "video_room_id"),
        Index("ix_bookings_trial_room_id", "trial_room_id"),
        Index("ix_bookings_wxcc_task_id", "wxcc_task_id"),
        Index("ix_bookings_contact_center_task_id", "contact_center_task_id"),
        CheckConstraint(
            "status IN ('confirmed', 'cancelled', 'completed', 'no_show', 'suspended', "
            "'awaiting_rebook', 'pending_confirmation', 'refused')",
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


class ShortfallWatchRow(Base):
    """Since when a slot has been short at the same counts (ADR 0057 point 6); no personal data."""

    __tablename__ = "shortfall_watches"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    slot_start: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


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
    image_id: Mapped[int | None] = mapped_column(
        ForeignKey("closure_images.id", ondelete="SET NULL")
    )


class ClosureImageRow(Base):
    """An image of a closure with its text alternative (ADR 0037 §3); metadata already stripped.

    Organisation data, not personal data: no purge. The type comes from the magic bytes.
    """

    __tablename__ = "closure_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[str] = mapped_column(String(16))
    digest: Mapped[str] = mapped_column(String(16))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    # The text alternative per locale as JSON; ``{}`` when decorative.
    alts: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    decorative: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))


class ClosureScenarioRow(Base):
    """A prepared closure content of an organisation or one group (ADR 0037 §4). No PII."""

    __tablename__ = "closure_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    # NULL: an organisation scenario, usable by every group of the organisation.
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    # Notices and headings per locale as JSON objects.
    notices: Mapped[str] = mapped_column(Text)
    headings: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    # The scenario's own contact block as JSON; NULL takes the group's block at the start.
    contacts: Mapped[str | None] = mapped_column(Text)
    image_id: Mapped[int | None] = mapped_column(
        ForeignKey("closure_images.id", ondelete="SET NULL")
    )

    # ADR 0099 point 2: the name is the natural key within the organisation or the group.
    __table_args__ = (
        Index(
            "uq_closure_scenarios_organisation_name",
            "organisation_id",
            "name",
            unique=True,
            sqlite_where=text("group_id IS NULL"),
        ),
        Index(
            "uq_closure_scenarios_group_name",
            "group_id",
            "name",
            unique=True,
            sqlite_where=text("group_id IS NOT NULL"),
        ),
    )


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

    Never holds personal data of participants; ``booking_id`` is a plain id without a foreign key,
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
    # The provider's reason for a failed job (ADR 0065): masked, capped, no participant data.
    message: Mapped[str] = mapped_column(String(300), server_default=text("''"))
    source: Mapped[str] = mapped_column(String(16), server_default=text("'admin'"))
    booking_id: Mapped[int | None] = mapped_column(Integer)
    # ADR 0040, part 2: the fields of the one event schema that are not derived on export.
    event_id: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), server_default=text("'info'"))
    category: Mapped[str] = mapped_column(String(16), server_default=text("'audit'"))
    trace_id: Mapped[str] = mapped_column(String(32), server_default=text("''"))
    service: Mapped[str] = mapped_column(String(16), server_default=text("''"))
    # ADR 0103 point 7: the role key that granted the admin the permission; never a name.
    actor_role: Mapped[str] = mapped_column(String(64), server_default=text("''"))

    __table_args__ = (
        Index("ix_audit_events_at", "at"),
        Index("ix_audit_events_organisation", "organisation"),
        Index("ix_audit_events_group", "group"),
        Index("ix_audit_events_source_at", "source", "at"),
        Index("ix_audit_events_event_id", "event_id", unique=True),
        Index("ix_audit_events_trace_id", "trace_id"),
        Index("ix_audit_events_category_at", "category", "at"),
        # ADR 0061 §7: the open alerts are the newest condition events per action.
        Index("ix_audit_events_action_at", "action", "at"),
        CheckConstraint(
            "outcome IN ('success', 'denied', 'failure', 'retry', 'skipped')", name="outcome"
        ),
        CheckConstraint("source IN ('admin', 'system')", name="source"),
        CheckConstraint("level IN ('debug', 'info', 'warning', 'error', 'critical')", name="level"),
        CheckConstraint(
            "category IN ('audit', 'system', 'security', 'diagnostic')", name="category"
        ),
    )


class DiagnosticModeRow(Base):
    """Diagnostic mode of one group or a whole organisation (ADR 0040, part 4).

    Slugs, not foreign keys, as in ``audit_events``: the mode ends by itself and is about the
    events, not the configuration. ``group`` is empty for a whole organisation, so one unique
    key covers both kinds of target.
    """

    __tablename__ = "diagnostic_modes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation: Mapped[str] = mapped_column(String(64))
    group: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    until: Mapped[datetime] = mapped_column(UTCDateTime())
    started_by: Mapped[str] = mapped_column(String(255))

    __table_args__ = (UniqueConstraint("organisation", "group", name="uq_diagnostic_modes_target"),)


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
    # ADR 0046 step 2: active, suspended or erased, and when it last changed.
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"), index=True)
    status_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # ADR 0117 step 10b: the verified ``email`` claim of the last login; personal data.
    email: Mapped[str | None] = mapped_column(String(320))
    email_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OverviewLayoutRow(Base):
    """An admin's own overview layout (ADR 0096): widget keys in order, comma-separated.

    Keys only, never personal data; erasing the staff record (ADR 0046) deletes it.
    """

    __tablename__ = "overview_layouts"

    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), primary_key=True
    )
    widget_keys: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class StaffMembershipRow(Base):
    """One grant of an admin as last seen: a role on an organisation or a group (ADR 0049)."""

    __tablename__ = "staff_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    role_key: Mapped[str] = mapped_column(String(40), server_default=text("''"))
    organisation: Mapped[str] = mapped_column(String(64))
    group: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_staff_memberships_staff_id", "staff_id"),
        Index("ix_staff_memberships_organisation", "organisation"),
        Index("ix_staff_memberships_group", "group"),
    )


class StaffRefusalRow(Base):
    """An entitlement value of an admin's last login that gave no right (ADR 0049 point 8).

    Holds the value (prefix, role key, IdP values), the reason code and the organisation it
    names, when known; never personal data.
    """

    __tablename__ = "staff_refused_entitlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"))
    value: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(40))
    organisation: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_staff_refused_entitlements_staff_id", "staff_id"),
        Index("ix_staff_refused_entitlements_organisation", "organisation"),
    )


class BookingEventRow(Base):
    """One entry of a booking's timeline (ADR 0010); the detail is a reason code."""

    __tablename__ = "booking_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(String(128), server_default=text("''"))
    message: Mapped[str] = mapped_column(String(300), server_default=text("''"))
    # ADR 0068: the APP_VERSION of the process that wrote the event.
    version: Mapped[str] = mapped_column(String(40), server_default=text("''"))

    __table_args__ = (Index("ix_booking_events_booking_id", "booking_id"),)


class BookingDayCountRow(Base):
    """Bookings of one group on one day that ended in one status (ADR 0102 step 4).

    What is left of a booking row after ``booking_row_retention_days``: a count, the day of
    the slot and the latest purge among them. No personal data and no identifier.
    """

    __tablename__ = "booking_day_counts"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(24), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    last_purged_at: Mapped[datetime] = mapped_column(UTCDateTime())


class UsageEventRow(Base):
    """One usage fact as written, the outbox of the rollup (ADR 0117 point 3).

    Hour-truncated time and the allow-listed codes and numbers only: no booking id, no
    reference, no free text, so a row cannot be joined back to an appointment. Staff facts
    carry ``staff_id``; every other row leaves it empty.
    """

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    bucket_hour: Mapped[datetime] = mapped_column(UTCDateTime())
    organisation_id: Mapped[int] = mapped_column(Integer)
    group_id: Mapped[int] = mapped_column(Integer)
    booking_kind: Mapped[str | None] = mapped_column(String(16))
    dispatch_kind: Mapped[str | None] = mapped_column(String(24))
    subject_code: Mapped[str | None] = mapped_column(String(40))
    locale: Mapped[str | None] = mapped_column(String(12))
    channel_kind: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(32))
    actor_class: Mapped[str | None] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(40))
    value_seconds: Mapped[int | None] = mapped_column(Integer)
    participants: Mapped[int | None] = mapped_column(Integer)
    capacity: Mapped[int | None] = mapped_column(Integer)
    booked: Mapped[int | None] = mapped_column(Integer)
    staff_id: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_usage_events_scope_hour", "organisation_id", "group_id", "bucket_hour"),
        Index("ix_usage_events_bucket_hour", "bucket_hour"),
        {"sqlite_autoincrement": True},  # an id below the watermark must never come back
    )


class UsageHourRow(Base):
    """The long-lived rollup: one row per hour, group, metric and cut (ADR 0117 point 3)."""

    __tablename__ = "usage_hour"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_hour: Mapped[datetime] = mapped_column(UTCDateTime())
    organisation_id: Mapped[int] = mapped_column(Integer)
    group_id: Mapped[int] = mapped_column(Integer)
    metric: Mapped[str] = mapped_column(String(8))
    booking_kind: Mapped[str | None] = mapped_column(String(16))
    dispatch_kind: Mapped[str | None] = mapped_column(String(24))
    subject_code: Mapped[str | None] = mapped_column(String(40))
    locale: Mapped[str | None] = mapped_column(String(12))
    channel_kind: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(32))
    actor_class: Mapped[str | None] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(40))
    bucket: Mapped[int | None] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    sum_value: Mapped[int] = mapped_column(Integer)
    max_value: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index(
            "ix_usage_hour_scope_metric_hour",
            "organisation_id",
            "group_id",
            "metric",
            "bucket_hour",
        ),
    )


class UsageStaffHourRow(Base):
    """The rollup per team member (ADR 0117 point 9); ``staff_id`` empty is *former members*."""

    __tablename__ = "usage_staff_hour"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_hour: Mapped[datetime] = mapped_column(UTCDateTime())
    organisation_id: Mapped[int] = mapped_column(Integer)
    group_id: Mapped[int] = mapped_column(Integer)
    staff_id: Mapped[int | None] = mapped_column(Integer)
    metric: Mapped[str] = mapped_column(String(8))
    count: Mapped[int] = mapped_column(Integer)
    sum_value: Mapped[int] = mapped_column(Integer)
    max_value: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index(
            "ix_usage_staff_hour_scope_metric_hour",
            "organisation_id",
            "group_id",
            "metric",
            "bucket_hour",
        ),
    )


class UsageRollupStateRow(Base):
    """The rollup's watermark: the last usage fact rolled into ``usage_hour`` (one row, id 1)."""

    __tablename__ = "usage_rollup_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_event_id: Mapped[int] = mapped_column(Integer)
    rolled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    backfilled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    backfill_cutoff: Mapped[datetime | None] = mapped_column(UTCDateTime())
    slots_closed_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class UsageBackfillRow(Base):
    """A booking already turned into usage facts by the backfill: a booking id, nothing else."""

    __tablename__ = "usage_backfill"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )


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
    # ADR 0082 point 11: the booking is in the own queue as a fallback.
    fallback: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    # ADR 0043: when the participant started waiting in the lobby, and when the worker
    # reported the booking as not picked up.
    lobby_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    not_picked_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # ADR 0117 step 3: the most people seen in the meeting, and when the first guest was seen.
    peak_participants: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    guest_joined_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


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


class IdentityProviderRow(Base):
    """An organisation's identity provider (ADR 0083 step 2).

    The client secret of ``client_secret_basic`` is sealed with AES-256-GCM under
    ``idp:<key>:secret`` (ADR 0008); every other column is public configuration.
    """

    __tablename__ = "identity_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    discovery_url: Mapped[str] = mapped_column(String(500))
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    client_auth: Mapped[str] = mapped_column(String(32))
    scopes: Mapped[str] = mapped_column(String(200))
    entitlement_claim: Mapped[str] = mapped_column(String(64))
    entitlement_prefix: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    mfa_policy: Mapped[str] = mapped_column(String(16), server_default="record")
    mfa_amr_values: Mapped[str] = mapped_column(String(1100), server_default="")
    mfa_acr_values: Mapped[str] = mapped_column(String(1100), server_default="")

    __table_args__ = (
        CheckConstraint(
            "client_auth IN ('private_key_jwt', 'client_secret_basic')", name="client_auth"
        ),
        CheckConstraint("mfa_policy IN ('off', 'record', 'require')", name="mfa_policy"),
    )


class LogDestinationRow(Base):
    """A log destination: where copies of the log lines go (ADR 0085).

    The credential (HEC token, API key, bearer token) is sealed with AES-256-GCM under
    ``logdest:<id>:secret`` (ADR 0008); every other column is public configuration.
    """

    __tablename__ = "log_destinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(500))
    index_name: Mapped[str] = mapped_column(String(120), server_default="")
    source: Mapped[str] = mapped_column(String(120), server_default="")
    min_level: Mapped[str] = mapped_column(String(16))
    categories: Mapped[str] = mapped_column(String(64))
    include_diagnostic: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    include_trace: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    ca_pem: Mapped[str] = mapped_column(Text, server_default="")
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("kind IN ('syslog', 'splunk_hec', 'elastic', 'http')", name="kind"),
    )


class LogDestinationStatusRow(Base):
    """What one process (``web`` or ``worker``) reports about one destination (ADR 0085).

    Counters and a machine reason only: no line, address or platform answer.
    """

    __tablename__ = "log_destination_status"

    destination_id: Mapped[int] = mapped_column(
        ForeignKey("log_destinations.id", ondelete="CASCADE"), primary_key=True
    )
    service: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(8))
    sent: Mapped[int] = mapped_column(Integer, server_default="0")
    dropped: Mapped[int] = mapped_column(Integer, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, server_default="0")
    queued: Mapped[int] = mapped_column(Integer, server_default="0")
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str] = mapped_column(String(16), server_default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class IdentityProviderKeyRow(Base):
    """A signing key pair of a provider (``private_key_jwt``, ADR 0083 point 6).

    The public JWK is published at ``/admin/jwks.json``; the private key is sealed under
    ``idp:<key>:key:<kid>`` and never leaves the server.
    """

    __tablename__ = "identity_provider_keys"

    kid: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("identity_providers.id", ondelete="CASCADE"), index=True
    )
    public_jwk: Mapped[str] = mapped_column(Text)
    key_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class IdentityProviderDomainRow(Base):
    """An e-mail domain claimed by a provider, with its TXT verification (ADR 0083 step 3)."""

    __tablename__ = "identity_provider_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("identity_providers.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    token: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    missing_since: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("state IN ('pending', 'verified', 'failing', 'lapsed')", name="state"),
    )


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
    requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # ADR 0116 point 9: the last support.json read, as text; the updates page parses it.
    support: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    # ADR 0116 point 9: the updater does not take the request before this moment.
    apply_after: Mapped[datetime | None] = mapped_column(UTCDateTime())

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


class CertificateCheckRow(Base):
    """The latest check of the public address's certificate (ADR 0064); at most one row."""

    __tablename__ = "certificate_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())
    host: Mapped[str] = mapped_column(String(255))
    finding: Mapped[str] = mapped_column(String(16))
    trust: Mapped[str | None] = mapped_column(String(16))
    error: Mapped[str] = mapped_column(String(40), server_default=text("''"))
    subject: Mapped[str | None] = mapped_column(String(255))
    issuer: Mapped[str | None] = mapped_column(String(255))
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime())
    not_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    names: Mapped[str] = mapped_column(Text, server_default=text("'[]'"))

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class InstanceRow(Base):
    """The installation's identity and the one pending probe nonce (ADR 0086); at most one row.

    ``instance_id`` is a random UUID made at first use, never editable and no secret. The nonce
    is kept as its ``sha256`` and used once.
    """

    __tablename__ = "instance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    probe_nonce_hash: Mapped[str | None] = mapped_column(String(64))
    probe_nonce_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class PublicAddressCheckRow(Base):
    """The latest round trip to one address, ``public`` or ``webhook`` (ADR 0086)."""

    __tablename__ = "public_address_checks"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())
    url: Mapped[str] = mapped_column(String(255))
    finding: Mapped[str] = mapped_column(String(24))
    scheme: Mapped[str | None] = mapped_column(String(8))
    forwarded: Mapped[str | None] = mapped_column(String(12))
    error: Mapped[str] = mapped_column(String(40), server_default=text("''"))


class PlatformSettingRow(Base):
    """The portal value of one class C setting (ADR 0042 §4), as JSON; never a secret."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OrganisationSettingRow(Base):
    """The value of one class D setting for one organisation (ADR 0042 step 5), as JSON."""

    __tablename__ = "organisation_settings"

    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SettingsVersionRow(Base):
    """The counter that goes up with every settings change (ADR 0042 §6); at most one row."""

    __tablename__ = "settings_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class RoleRow(Base):
    """A custom role (ADR 0039): a copy of a built-in template within its bounds.

    ``permissions`` is space separated; a permission no longer known grants nothing. The
    reach follows from ``template`` and never changes.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template: Mapped[str] = mapped_column(String(32))
    name_nl: Mapped[str] = mapped_column(String(80))
    name_en: Mapped[str] = mapped_column(String(80))
    claim_value: Mapped[str] = mapped_column(String(128), unique=True)
    permissions: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)


class RunningVersionRow(Base):
    """A version this installation ran, noted by the web process at start-up (ADR 0067)."""

    __tablename__ = "running_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(20))
    commit: Mapped[str] = mapped_column(String(40))
    since: Mapped[datetime] = mapped_column(UTCDateTime())
    request_id: Mapped[int | None] = mapped_column(Integer)


class ReleaseListingRow(Base):
    """A release or candidate the last check listed, with its notes (ADR 0067)."""

    __tablename__ = "release_listing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(20), unique=True)
    release_url: Mapped[str] = mapped_column(String(300))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))


class OwnCertificateRow(Base):
    """The own certificate uploaded in the portal (ADR 0072); at most one row.

    The chain is public and stored as text with the leaf's public key; the private key is never
    in the database (it lives on the volume the proxy reads), so a backup never carries it.
    """

    __tablename__ = "own_certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_pem: Mapped[str] = mapped_column(Text)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    fingerprint: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str] = mapped_column(String(255))
    not_before: Mapped[datetime] = mapped_column(UTCDateTime())
    not_after: Mapped[datetime] = mapped_column(UTCDateTime())
    names: Mapped[str] = mapped_column(Text, server_default=text("'[]'"))
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class AdminSessionRow(Base):
    """One admin session on the server (ADR 0098).

    ``id_hash`` is the ``sha256`` of the random reference the cookie holds; the reference
    itself is never stored. ``claims`` is the JSON of what the ID token said. Staff data, not
    participant PII.
    """

    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_hash: Mapped[str] = mapped_column(String(64), unique=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    claims: Mapped[str] = mapped_column(Text)
    login_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    end_reason: Mapped[str | None] = mapped_column(String(16))


class ParticipantLinkSessionRow(Base):
    """One browser that opened a management link (ADR 0093).

    ``value_hash`` is the ``sha256`` of the random cookie value; the value itself is never
    stored. No personal data: the session names bookings by id only.
    """

    __tablename__ = "participant_link_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class ParticipantLinkBookingRow(Base):
    """A booking that a participant link session holds (at most 20 per session, ADR 0093)."""

    __tablename__ = "participant_link_bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("participant_link_sessions.id", ondelete="CASCADE")
    )
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    added_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        UniqueConstraint(
            "session_id", "booking_id", name="uq_participant_link_bookings_session_booking"
        ),
    )


class AlertTargetRow(Base):
    """Where alerts go (ADR 0082 point 9): an administrator's email address or a Webex room.

    ``organisation_id`` NULL is a target of the platform. No participant data.
    """

    __tablename__ = "alert_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(320))
    min_level: Mapped[str] = mapped_column(String(16))

    __table_args__ = (
        CheckConstraint("kind IN ('email', 'webex_space')", name="kind"),
        CheckConstraint("min_level IN ('warning', 'outage')", name="min_level"),
        # ADR 0099 point 2: kind and address are the natural key within the owner.
        Index(
            "uq_alert_targets_organisation_address",
            "organisation_id",
            "kind",
            "address",
            unique=True,
            sqlite_where=text("organisation_id IS NOT NULL"),
        ),
        Index(
            "uq_alert_targets_platform_address",
            "kind",
            "address",
            unique=True,
            sqlite_where=text("organisation_id IS NULL"),
        ),
    )


class AlertDeliveryRow(Base):
    """What was sent of one alert to one target: repeat, dedupe and retry (ADR 0082 point 9)."""

    __tablename__ = "alert_deliveries"

    target_id: Mapped[int] = mapped_column(
        ForeignKey("alert_targets.id", ondelete="CASCADE"), primary_key=True
    )
    alert_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    token: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RescheduleProposalRow(Base):
    """A proposed new time holding one place in its slot until answered (ADR 0057 point 7).

    No personal data: the booking holds it. The place counts while ``released_at`` is empty and
    the booking still awaits a new time (``uow.get_slot_usage``, ``count_active_bookings``).
    """

    __tablename__ = "reschedule_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id", ondelete="CASCADE"))
    start: Mapped[datetime] = mapped_column(UTCDateTime())
    end: Mapped[datetime] = mapped_column(UTCDateTime())
    confirm_by: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('confirmed', 'chosen', 'cancelled', 'expired', 'withdrawn')",
            name="outcome",
        ),
        Index("ix_reschedule_proposals_booking_id", "booking_id"),
        Index("ix_reschedule_proposals_slot_id", "slot_id"),
    )


class ChangeRequestRow(Base):
    """A bot's request to cancel or move a booking (ADR 0023 step 2); no personal data.

    ``booking_id`` is empty when the phone number and code matched no booking that could still
    be changed: the request then only expires, as the API answered the same either way. A
    request with ``target_start`` moves the booking there; one without it cancels.
    """

    __tablename__ = "change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True)
    client_id: Mapped[str] = mapped_column(String(64))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    target_start: Mapped[datetime | None] = mapped_column(UTCDateTime())
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    pinned_start: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_change_requests_booking_id", "booking_id"),
        Index("ix_change_requests_group_id", "group_id"),
    )


class ReportSubscriptionRow(Base):
    """A digest subscription of the usage reports (ADR 0117 point 16).

    ``recipients`` holds staff row ids, comma-separated; never an address. The address is read
    from the identity provider's directory when the mail goes out.
    """

    __tablename__ = "report_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey("organisations.id", ondelete="CASCADE"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(8))
    weekday: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    month_day: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    recipients: Mapped[str] = mapped_column(String(1000), server_default=text("''"))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("period IN ('daily', 'weekly', 'monthly')", name="period"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("month_day BETWEEN 1 AND 28", name="month_day"),
        Index("ix_report_subscriptions_organisation_id", "organisation_id"),
    )


class ReportDigestRunRow(Base):
    """One due digest and what came of it; unique per subscription and due date."""

    __tablename__ = "report_digest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("report_subscriptions.id", ondelete="CASCADE")
    )
    due_date: Mapped[date] = mapped_column(Date())
    status: Mapped[str] = mapped_column(String(16))
    sent: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    ran_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("status IN ('sent', 'skipped_late', 'no_recipient')", name="status"),
        UniqueConstraint("subscription_id", "due_date"),
    )


class WallboardLinkRow(Base):
    """A display link of a wallboard (ADR 0117 point 20, step 14).

    Only ``sha256`` of the token and of the screen's session secret are stored, never the
    secrets. ``pinned_network`` and ``networks`` are ranges of staff equipment, not of a
    participant; they live on this row alone and go with it.
    """

    __tablename__ = "wallboard_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey("organisations.id", ondelete="CASCADE"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    session_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_refused_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    networks: Mapped[str] = mapped_column(String(1500), server_default=text("''"))
    pin: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    pinned_network: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_wallboard_links_organisation_id", "organisation_id"),
        Index("ix_wallboard_links_session_hash", "session_hash"),
    )


class SkillRow(Base):
    """One skill of an organisation's catalogue (ADR 0121 point 5); no personal data.

    ``labels`` is a JSON object of locale to label, like the labels of a subject (ADR 0081).
    """

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey("organisations.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    labels: Mapped[str] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (UniqueConstraint("organisation_id", "code"),)


class StaffSkillRow(Base):
    """A skill a staff member holds (ADR 0121 point 5); data about staff, erased with them."""

    __tablename__ = "staff_skills"

    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))

    __table_args__ = (Index("ix_staff_skills_skill_id", "skill_id"),)


class CapacityPoolRow(Base):
    """A capacity pool of an organisation (ADR 0121 point 6).

    A ``legacy_group`` pool wraps one group's present staffing (``group_id`` set, unique); a
    ``counted`` pool belongs to the organisation alone and has a stable ``code`` (NULL only for a
    legacy pool, and for a counted one written by an image from before migration 0110).
    """

    __tablename__ = "capacity_pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey("organisations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(16))
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), unique=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    code: Mapped[str | None] = mapped_column(String(40))
    # ADR 0122 step 2: the zone the pool's weekly blocks are local to; NULL for a legacy pool
    # and for a counted pool that has no staffing yet.
    timezone: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("mode IN ('legacy_group', 'counted')", name="mode"),
        CheckConstraint("(mode = 'legacy_group') = (group_id IS NOT NULL)", name="group_mode"),
        Index("ix_capacity_pools_organisation_id", "organisation_id"),
        Index(
            "uq_capacity_pools_organisation_id_code",
            "organisation_id",
            "code",
            unique=True,
            sqlite_where=text("code IS NOT NULL"),
        ),
    )


class CapacityPoolSkillRow(Base):
    """A skill every counted unit of a pool holds."""

    __tablename__ = "capacity_pool_skills"

    pool_id: Mapped[int] = mapped_column(
        ForeignKey("capacity_pools.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )


class ServiceFlowRow(Base):
    """A flow of a group (ADR 0121 point 3).

    ``current_revision`` names the published revision in effect, NULL for a flow never published;
    ``draft_revision`` the unpublished revision being edited, NULL when there is none. At most one
    active flow per group is the default of each entry path (partial unique indexes).
    """

    __tablename__ = "service_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(16))
    default_appointment: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    default_on_demand: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    current_revision: Mapped[int | None] = mapped_column(Integer)
    draft_revision: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("group_id", "code"),
        CheckConstraint("state IN ('draft', 'active', 'archived')", name="state"),
        CheckConstraint(
            "state != 'active' OR current_revision IS NOT NULL", name="active_has_current_revision"
        ),
        Index(
            "uq_service_flows_default_appointment",
            "group_id",
            unique=True,
            sqlite_where=text("default_appointment = 1 AND state = 'active'"),
        ),
        Index(
            "uq_service_flows_default_on_demand",
            "group_id",
            unique=True,
            sqlite_where=text("default_on_demand = 1 AND state = 'active'"),
        ),
    )


class FlowRevisionRow(Base):
    """One revision of a flow; immutable once ``published_at`` is set (ADR 0121 point 3).

    ``intake_modes`` is a comma-separated list, ``labels`` a JSON object. ``location_id`` is a
    plain number: locations do not exist yet.
    """

    __tablename__ = "flow_revisions"

    flow_id: Mapped[int] = mapped_column(
        ForeignKey("service_flows.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    labels: Mapped[str] = mapped_column(Text)
    intake_modes: Mapped[str] = mapped_column(String(32))
    interaction_kind: Mapped[str | None] = mapped_column(String(16))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    # NO ACTION, checked at the end of the statement: pools are archived, never deleted, so
    # deleting a pool on its own fails while a revision draws on it, yet deleting an organisation
    # lets the cascades remove the pool and the revisions in one statement (RESTRICT fails there).
    capacity_pool_id: Mapped[int | None] = mapped_column(
        ForeignKey("capacity_pools.id", ondelete="NO ACTION")
    )
    dispatch: Mapped[str | None] = mapped_column(String(16))
    handling_action: Mapped[str | None] = mapped_column(String(24))
    completion: Mapped[str | None] = mapped_column(String(32))
    location_id: Mapped[int | None] = mapped_column(Integer)
    collects_email: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    legacy: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    published_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))

    __table_args__ = (Index("ix_flow_revisions_capacity_pool_id", "capacity_pool_id"),)


class FlowRevisionSkillRow(Base):
    """A skill every handler of a revision needs (ALL_OF)."""

    __tablename__ = "flow_revision_skills"

    flow_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["flow_id", "revision"],
            ["flow_revisions.flow_id", "flow_revisions.revision"],
            ondelete="CASCADE",
        ),
        Index("ix_flow_revision_skills_skill_id", "skill_id"),
    )


class SubjectFlowRow(Base):
    """A subject a revision offers; the subject's code stays where ADR 0081 put it."""

    __tablename__ = "subject_flows"

    flow_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_code: Mapped[str] = mapped_column(String(40), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["flow_id", "revision"],
            ["flow_revisions.flow_id", "flow_revisions.revision"],
            ondelete="CASCADE",
        ),
    )


class SubjectFlowSkillRow(Base):
    """An extra skill the combination of a subject and a revision requires."""

    __tablename__ = "subject_flow_skills"

    flow_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["flow_id", "revision", "subject_code"],
            ["subject_flows.flow_id", "subject_flows.revision", "subject_flows.subject_code"],
            ondelete="CASCADE",
        ),
        Index("ix_subject_flow_skills_skill_id", "skill_id"),
    )
