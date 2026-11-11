"""Skills, capacity pools and service flows (ADR 0121 step 2b).

Expand only (ADR 0116): nine new tables and two nullable columns on ``bookings``; the previous
image neither reads nor writes any of them, and no existing column changes meaning.

- ``skills``, ``staff_skills``: an organisation's skill catalogue and who holds which skill.
- ``capacity_pools``, ``capacity_pool_skills``: whose time a flow draws on.
- ``service_flows``, ``flow_revisions``, ``flow_revision_skills``, ``subject_flows``,
  ``subject_flow_skills``: the flows of a group and their immutable revisions.
- ``bookings.flow_id``, ``bookings.flow_revision``: both NULL for every existing booking, which
  reads as the group's default flow for its entry path.

Backfill, for every group (archived ones too): a ``legacy_group`` pool; a flow ``appointment``
(the default of the appointment path) and, when a weekly staffing block of the group offers
direct calls (``offer`` direct or both, capacity above zero, whatever its validity dates: the
flow is configuration, and today's date plays no part), a flow ``direct`` (the default of the
on-demand path). Revision 1 of each is the
wrapper of ``app.domain.service_flows.legacy_revision`` and is the current revision
(``current_revision`` 1, no draft; a flow never published has ``current_revision`` NULL); the
literal values are written here
because a migration imports no application code, and a test compares the two.

Revision ID: 0109
Revises: 0108
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.adapters.db.types import UTCDateTime

revision: str = "0109"
down_revision: str | None = "0108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_LABEL = 80
"""The longest label or pool name the domain accepts; a longer group name is cut to it."""


def _fk(column: str, target: str, table: str, ondelete: str) -> sa.ForeignKeyConstraint:
    """Return a single-column foreign key named like the naming convention does."""
    referred_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=op.f(f"fk_{table}_{column}_{referred_table}"),
        ondelete=ondelete,
    )


def _revision_fk(table: str, columns: list[str], target: str) -> sa.ForeignKeyConstraint:
    """Return the composite foreign key of a row that belongs to one revision or subject."""
    return sa.ForeignKeyConstraint(
        columns,
        [f"{target}.{column}" for column in columns],
        name=op.f(f"fk_{table}_flow_id_{target}"),
        ondelete="CASCADE",
    )


def _create_catalogue() -> None:
    """Create the skill and pool tables."""
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("labels", sa.Text(), nullable=False),
        sa.Column("archived_at", UTCDateTime(), nullable=True),
        _fk("organisation_id", "organisations.id", "skills", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("organisation_id", "code", name=op.f("uq_skills_organisation_id_code")),
    )
    op.create_table(
        "staff_skills",
        sa.Column("staff_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("granted_at", UTCDateTime(), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        _fk("staff_id", "staff.id", "staff_skills", "CASCADE"),
        _fk("skill_id", "skills.id", "staff_skills", "CASCADE"),
        _fk("granted_by", "staff.id", "staff_skills", "SET NULL"),
        sa.PrimaryKeyConstraint("staff_id", "skill_id", name=op.f("pk_staff_skills")),
    )
    op.create_index("ix_staff_skills_skill_id", "staff_skills", ["skill_id"])
    op.create_table(
        "capacity_pools",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("archived_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            "mode IN ('legacy_group', 'counted')", name=op.f("ck_capacity_pools_mode")
        ),
        sa.CheckConstraint(
            "(mode = 'legacy_group') = (group_id IS NOT NULL)",
            name=op.f("ck_capacity_pools_group_mode"),
        ),
        _fk("organisation_id", "organisations.id", "capacity_pools", "CASCADE"),
        _fk("group_id", "groups.id", "capacity_pools", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capacity_pools")),
        sa.UniqueConstraint("group_id", name=op.f("uq_capacity_pools_group_id")),
    )
    op.create_index("ix_capacity_pools_organisation_id", "capacity_pools", ["organisation_id"])
    op.create_table(
        "capacity_pool_skills",
        sa.Column("pool_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        _fk("pool_id", "capacity_pools.id", "capacity_pool_skills", "CASCADE"),
        _fk("skill_id", "skills.id", "capacity_pool_skills", "CASCADE"),
        sa.PrimaryKeyConstraint("pool_id", "skill_id", name=op.f("pk_capacity_pool_skills")),
    )


def _create_flows() -> None:
    """Create the flow tables."""
    op.create_table(
        "service_flows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("default_appointment", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("default_on_demand", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=True),
        sa.Column("draft_revision", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "state IN ('draft', 'active', 'archived')", name=op.f("ck_service_flows_state")
        ),
        _fk("group_id", "groups.id", "service_flows", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_flows")),
        sa.UniqueConstraint("group_id", "code", name=op.f("uq_service_flows_group_id_code")),
    )
    op.create_index(
        "uq_service_flows_default_appointment",
        "service_flows",
        ["group_id"],
        unique=True,
        sqlite_where=sa.text("default_appointment = 1 AND state = 'active'"),
    )
    op.create_index(
        "uq_service_flows_default_on_demand",
        "service_flows",
        ["group_id"],
        unique=True,
        sqlite_where=sa.text("default_on_demand = 1 AND state = 'active'"),
    )
    op.create_table(
        "flow_revisions",
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("labels", sa.Text(), nullable=False),
        sa.Column("intake_modes", sa.String(length=32), nullable=False),
        sa.Column("interaction_kind", sa.String(length=16), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("capacity_pool_id", sa.Integer(), nullable=True),
        sa.Column("dispatch", sa.String(length=16), nullable=True),
        sa.Column("handling_action", sa.String(length=24), nullable=True),
        sa.Column("completion", sa.String(length=32), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("collects_email", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("legacy", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("published_at", UTCDateTime(), nullable=True),
        sa.Column("published_by", sa.Integer(), nullable=True),
        _fk("flow_id", "service_flows.id", "flow_revisions", "CASCADE"),
        _fk("capacity_pool_id", "capacity_pools.id", "flow_revisions", "RESTRICT"),
        _fk("published_by", "staff.id", "flow_revisions", "SET NULL"),
        sa.PrimaryKeyConstraint("flow_id", "revision", name=op.f("pk_flow_revisions")),
    )
    op.create_index("ix_flow_revisions_capacity_pool_id", "flow_revisions", ["capacity_pool_id"])
    op.create_table(
        "flow_revision_skills",
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        _revision_fk("flow_revision_skills", ["flow_id", "revision"], "flow_revisions"),
        _fk("skill_id", "skills.id", "flow_revision_skills", "CASCADE"),
        sa.PrimaryKeyConstraint(
            "flow_id", "revision", "skill_id", name=op.f("pk_flow_revision_skills")
        ),
    )
    op.create_index("ix_flow_revision_skills_skill_id", "flow_revision_skills", ["skill_id"])
    op.create_table(
        "subject_flows",
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("subject_code", sa.String(length=40), nullable=False),
        _revision_fk("subject_flows", ["flow_id", "revision"], "flow_revisions"),
        sa.PrimaryKeyConstraint(
            "flow_id", "revision", "subject_code", name=op.f("pk_subject_flows")
        ),
    )
    op.create_table(
        "subject_flow_skills",
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("subject_code", sa.String(length=40), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        _revision_fk(
            "subject_flow_skills", ["flow_id", "revision", "subject_code"], "subject_flows"
        ),
        _fk("skill_id", "skills.id", "subject_flow_skills", "CASCADE"),
        sa.PrimaryKeyConstraint(
            "flow_id", "revision", "subject_code", "skill_id", name=op.f("pk_subject_flow_skills")
        ),
    )
    op.create_index("ix_subject_flow_skills_skill_id", "subject_flow_skills", ["skill_id"])


def _short(name: str) -> str:
    """Return ``name`` as a label: stripped, at most ``MAX_LABEL`` long, never empty."""
    return name.strip()[:MAX_LABEL].strip() or "-"


def _backfill() -> None:
    """Give every group its legacy pool and its default flows."""
    bind = op.get_bind()
    groups = sa.table(
        "groups",
        sa.column("id"),
        sa.column("organisation_id"),
        sa.column("name"),
        sa.column("slot_minutes"),
        sa.column("dispatch_kind"),
        sa.column("archived_at"),
    )
    rules = sa.table(
        "schedule_rules",
        sa.column("group_id"),
        sa.column("offer"),
        sa.column("capacity"),
    )
    meta = sa.MetaData()
    pools = sa.Table(
        "capacity_pools",
        meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organisation_id", sa.Integer()),
        sa.Column("name", sa.String()),
        sa.Column("mode", sa.String()),
        sa.Column("group_id", sa.Integer()),
    )
    flows = sa.Table(
        "service_flows",
        meta,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer()),
        sa.Column("code", sa.String()),
        sa.Column("state", sa.String()),
        sa.Column("default_appointment", sa.Boolean()),
        sa.Column("default_on_demand", sa.Boolean()),
        sa.Column("current_revision", sa.Integer()),
        sa.Column("draft_revision", sa.Integer()),
    )
    revisions = sa.table(
        "flow_revisions",
        sa.column("flow_id"),
        sa.column("revision"),
        sa.column("labels"),
        sa.column("intake_modes"),
        sa.column("interaction_kind"),
        sa.column("duration_minutes"),
        sa.column("capacity_pool_id"),
        sa.column("dispatch"),
        sa.column("handling_action"),
        sa.column("completion"),
        sa.column("collects_email"),
        sa.column("legacy"),
        sa.column("published_at", UTCDateTime()),
    )
    now = datetime.now(UTC)
    # The flow is configuration: whether a direct page is open today stays decided by the rules,
    # so the validity dates of a rule do not matter here, and the result never depends on the day.
    direct = set(
        bind.execute(
            sa.select(rules.c.group_id).where(
                rules.c.offer.in_(("direct", "both")), rules.c.capacity > 0
            )
        ).scalars()
    )
    for group in bind.execute(sa.select(groups).order_by(groups.c.id)).all():
        pool_id = bind.execute(
            sa.insert(pools).values(
                organisation_id=group.organisation_id,
                name=_short(group.name),
                mode="legacy_group",
                group_id=group.id,
            )
        ).inserted_primary_key[0]
        dispatch = "own_queue" if group.dispatch_kind == "openplanner_queue" else "contact_center"
        state = "archived" if group.archived_at is not None else "active"
        offered = [("appointment", "appointment", "default_appointment")]
        if group.id in direct:
            offered.append(("direct", "on_demand", "default_on_demand"))
        for code, intake, default in offered:
            flow_id = bind.execute(
                sa.insert(flows).values(
                    group_id=group.id,
                    code=code,
                    state=state,
                    default_appointment=default == "default_appointment",
                    default_on_demand=default == "default_on_demand",
                    current_revision=1,
                    draft_revision=None,
                )
            ).inserted_primary_key[0]
            bind.execute(
                sa.insert(revisions).values(
                    flow_id=flow_id,
                    revision=1,
                    labels=json.dumps({"nl": _short(group.name), "en": _short(group.name)}),
                    intake_modes=intake,
                    interaction_kind="video",
                    duration_minutes=group.slot_minutes,
                    capacity_pool_id=pool_id,
                    dispatch=dispatch,
                    handling_action="open_video",
                    completion="legacy_video_observation",
                    collects_email=True,
                    legacy=True,
                    published_at=now,
                )
            )


def upgrade() -> None:
    """Create the tables, add the two booking columns and backfill the legacy pools and flows."""
    _create_catalogue()
    _create_flows()
    op.add_column("bookings", sa.Column("flow_id", sa.Integer(), nullable=True))
    op.add_column("bookings", sa.Column("flow_revision", sa.Integer(), nullable=True))
    _backfill()


def downgrade() -> None:
    """Drop the two booking columns and the nine tables; nothing else referred to them."""
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("flow_revision")
        batch.drop_column("flow_id")
    op.drop_index("ix_subject_flow_skills_skill_id", table_name="subject_flow_skills")
    op.drop_table("subject_flow_skills")
    op.drop_table("subject_flows")
    op.drop_index("ix_flow_revision_skills_skill_id", table_name="flow_revision_skills")
    op.drop_table("flow_revision_skills")
    op.drop_index("ix_flow_revisions_capacity_pool_id", table_name="flow_revisions")
    op.drop_table("flow_revisions")
    op.drop_index("uq_service_flows_default_on_demand", table_name="service_flows")
    op.drop_index("uq_service_flows_default_appointment", table_name="service_flows")
    op.drop_table("service_flows")
    op.drop_table("capacity_pool_skills")
    op.drop_index("ix_capacity_pools_organisation_id", table_name="capacity_pools")
    op.drop_table("capacity_pools")
    op.drop_index("ix_staff_skills_skill_id", table_name="staff_skills")
    op.drop_table("staff_skills")
    op.drop_table("skills")
