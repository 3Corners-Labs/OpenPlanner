"""A service chooses the call page and what is offered next to it (ADR 0123 step 6a).

Expand only (ADR 0116): add and widen; nothing is dropped, tightened or backfilled.

* ``groups.join_client`` may now be ``embedded`` (the call in our own page). The check is
  widened, which SQLite does by copying the table; no row changes. Every group keeps the value
  it has.
* ``groups.embedded_call_webex_option`` (new, nullable, checked): whether the participant of an
  embedded call is ever shown a way into the video platform's own app. The organisation's value
  stays the default and a service may set its own, for good: NULL means "as the organisation".
  Nothing is backfilled, so after the upgrade every service follows its organisation exactly as
  before, and a later change of the organisation's value still reaches it.

The previous image does not know ``embedded``: it reads ``join_client`` through
``JoinClient(row.join_client)``, so a group saved as ``embedded`` by this image makes it fail to
read that group. The step ships dark (``embedded_call_enabled`` is off and a group can be saved as
``embedded`` only after the guest check passed), so a rollback before anybody chose it is safe.
After that, set every such group to ``browser`` before going back to the previous image (the
group's settings form, or the configuration package); the downgrade below does it by itself, but
only when the schema is downgraded.

Downgrade turns every ``embedded`` group into ``browser`` (what the previous image would have
done for a participant: the redirect), narrows the check again and drops the column; a service's
own option is lost, the organisation's value was never touched.

Revision ID: 0119
Revises: 0118
"""

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
    """Allow ``embedded`` and add the service's own option (empty: as the organisation)."""
    with op.batch_alter_table("groups") as batch:
        batch.add_column(sa.Column("embedded_call_webex_option", sa.String(length=16)))
        batch.drop_constraint(op.f("ck_groups_join_client"), type_="check")
        batch.create_check_constraint(
            op.f("ck_groups_join_client"), "join_client IN ('browser', 'choice', 'embedded')"
        )
        batch.create_check_constraint(
            op.f("ck_groups_embedded_call_webex_option"), OPTION_CHECK.format(OPTIONS)
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
