"""The optional ``flow`` a participant-facing entry path may carry (ADR 0121 step 3a).

A flow code is a stable, non-personal code (the grammar of a subject code), so it may appear in
a URL, a form and a request body. The pages read it from the query or the form, the widget API
from its JSON body; the domain resolves it (``BookingService.flow_for``, ``book``,
``start_direct``) and refuses one that does not resolve, never falling back to the default.
"""

from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import SUBJECT_CODE


def flow_param(value: object) -> str | None:
    """Return the flow code a query or form carries, ``None`` when it is absent or empty.

    An empty value is "not asked for" (a form that left the field blank); anything else must
    have the grammar of a flow code, before any query is made.

    Raises:
        DomainError: ``flow_not_available`` (field ``flow``) for a value that is not a code.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not SUBJECT_CODE.fullmatch(value):
        raise DomainError(ErrorCode.FLOW_NOT_AVAILABLE, "flow")
    return value
