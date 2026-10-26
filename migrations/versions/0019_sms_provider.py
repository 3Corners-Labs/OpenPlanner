"""SMS provider as data (ADR 0028): stored SMS settings name their gateway.

Every SMS settings row so far is a Webex Connect service, so the upgrade adds
``"provider": "webex_connect"`` to its JSON. The downgrade deletes rows of any other
provider, which the old code would send through Webex Connect, and strips the key from the
rest; their sealed API key stays as it is.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-26 22:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE channel_settings "
        "SET settings = json_set(settings, '$.provider', 'webex_connect') "
        "WHERE channel = 'sms'"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM channel_settings WHERE channel = 'sms' "
        "AND json_extract(settings, '$.provider') != 'webex_connect'"
    )
    op.execute(
        "UPDATE channel_settings SET settings = json_remove(settings, '$.provider') "
        "WHERE channel = 'sms'"
    )
