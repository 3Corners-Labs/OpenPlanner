"""The interaction kinds of a flow: what the participant and staff do together (ADR 0121 point 4).

A module of its own so that both the flows (``app.domain.service_flows``) and the
contact-center contract (``app.domain.contact_center``, ADR 0121 step 6) can name the closed
list without importing each other. The list is fixed: adding a kind changes the closed table
of ADR 0121 point 4 and needs an ADR.
"""

from enum import StrEnum


class InteractionKind(StrEnum):
    """What the participant and staff do together."""

    VIDEO = "video"
    TELEPHONE = "telephone"
    IN_PERSON = "in_person"
    NONE = "none"
