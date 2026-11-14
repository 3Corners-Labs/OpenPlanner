"""A service chooses the call page and what is offered next to it (ADR 0123 step 6a).

Expand only (ADR 0116): add, widen, backfill; nothing is dropped or tightened.

* ``groups.join_client`` may now be ``embedded`` (the call in our own page). The check is
  widened, which SQLite does by copying the table; no row changes. Every group keeps the value
  it has.
* ``groups.embedded_call_webex_option`` (new, nullable, checked): whether the participant of an
  embedded call is ever shown a way into the video platform's own app, a service's setting from
  now on. NULL reads as "the organisation's value", which the call page and the join path still
  honour for one minor (a persisted name moves by writing the new place and reading both, ADR
  0116). The backfill writes the organisation's own stored value, where it has one, into each
  of its groups, so a group acts as it did before; an organisation without a stored value
  leaves its groups at NULL, which keeps following the installation's value.

The previous image does not know ``embedded``: a group saved as ``embedded`` by this image makes
the previous image fail to read that group. The step ships dark (``embedded_call_enabled`` is off
and a group can be saved as ``embedded`` only after the guest check passed), so a rollback before
anybody chose it is safe; after that, set such a group back to ``browser`` first.

Downgrade turns every ``embedded`` group into ``browser`` (what the previous image would have
done for a participant: the redirect), narrows the check again and drops the column; the
per-service option is lost, the organisation's own stored value was never touched.

Revision ID: 0119
Revises: 0118
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0119"
down_revision: str | None = "0118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPTIONS = ("hidden", "on_failure", "always")
"""The values of ``embedded_call_webex_option``."""
OPTION_CHECK = "embedded_call_webex_option IS NULL OR embedded_call_webex_option IN {}"


def upgrade() -> None:
    """Allow ``embedded``, add the service's option and copy the organisation's value into it."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(sa.Column("embedded_call_webex_option", sa.String(length=16)))
        batch.drop_constraint(op.f("ck_groups_join_client"), type_="check")
        batch.create_check_constraint(
            op.f("ck_groups_join_client"), "join_client IN ('browser', 'choice', 'embedded')"
        )
        batch.create_check_constraint(
            op.f("ck_groups_embedded_call_webex_option"), OPTION_CHECK.format(OPTIONS)
        )
    bind = op.get_bind()
    stored = bind.execute(
        sa.text(
            "SELECT organisation_id, value FROM organisation_settings"
            " WHERE key = 'embedded_call_webex_option'"
        )
    ).all()
    for organisation_id, raw in stored:
        try:
            value = json.loads(raw)
        except ValueError:
            continue  # not a value the setting could hold: the group keeps following the default
        if value in OPTIONS:
            bind.execute(
                sa.text(
                    "UPDATE groups SET embedded_call_webex_option = :value"
                    " WHERE organisation_id = :organisation"
                ),
                {"value": value, "organisation": organisation_id},
            )


def downgrade() -> None:
    """Give every embedded group the default client, narrow the check and drop the option."""
    op.execute("UPDATE groups SET join_client = 'browser' WHERE join_client = 'embedded'")
    with op.batch_alter_table("groups") as batch:
        batch.drop_constraint(op.f("ck_groups_embedded_call_webex_option"), type_="check")
        batch.drop_column("embedded_call_webex_option")
        batch.drop_constraint(op.f("ck_groups_join_client"), type_="check")
        batch.create_check_constraint(
            op.f("ck_groups_join_client"), "join_client IN ('browser', 'choice')"
        )
