"""The one SQL condition for "the revisions of a flow that are in effect or being edited".

Skills and capacity pools ask where they are used; a flow uses what its published (current)
revision and its draft name, and nothing in older revisions (ADR 0121 point 3).
"""

from sqlalchemy import ColumnElement, and_, or_

from app.adapters.db.models import FlowRevisionRow, ServiceFlowRow

REVISION_IN_EFFECT: ColumnElement[bool] = and_(
    FlowRevisionRow.flow_id == ServiceFlowRow.id,
    or_(
        FlowRevisionRow.revision == ServiceFlowRow.current_revision,
        FlowRevisionRow.revision == ServiceFlowRow.draft_revision,
    ),
)
"""Join condition: the revision row is the current or the draft revision of the flow row."""

CURRENT_REVISION: ColumnElement[bool] = and_(
    FlowRevisionRow.flow_id == ServiceFlowRow.id,
    FlowRevisionRow.revision == ServiceFlowRow.current_revision,
)
"""Join condition: the revision row is the published revision of the flow row."""
