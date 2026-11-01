"""The booking form summary on a group's settings page (ADR 0055, ADR 0047).

The booking form is a sub-page of the settings; this card names its questions so an administrator
sees that it exists and what it asks, without opening it. It lives apart from ``form_routes``
because that module imports the group pages and the settings page imports this one.
"""

from dataclasses import dataclass

from fastapi import Request

from app.admin.authz import AdminContext
from app.domain.form_service import FormService
from app.web.locale import RequestLocale

SHOWN_LABELS = 4
"""How many question labels the card names before it says how many more there are."""


@dataclass(frozen=True)
class FormSummary:
    """What the settings card shows about the booking form.

    Attributes:
        labels: The labels of the first questions, in the administrator's language.
        more: How many further questions are not named.
        count: The number of own questions.
        limit: The platform's maximum number of own questions (class C).
    """

    labels: tuple[str, ...]
    more: int
    count: int
    limit: int


async def form_summary(
    request: Request, locale: RequestLocale, context: AdminContext
) -> FormSummary:
    """Return the summary of the booking form of the context's group."""
    service: FormService = request.app.state.form_service
    found = await service.view(context.principal, context.group.group.slug)
    labels = tuple(f.label(locale.code) for f in found.form.fields)
    return FormSummary(
        labels=labels[:SHOWN_LABELS],
        more=max(0, len(labels) - SHOWN_LABELS),
        count=len(labels),
        limit=found.max_custom_fields,
    )
