"""Shared plumbing of the participant pages: templates, the organisation's branding, rendering.

Every participant page of a group goes through ``participant_page``, so each shows the logo, name
and accent colour of the group's organisation (WP3b) and may load the page's own scripts
(ADR 0020), and shows the organisation's own wording (ADR 0074). The branding reaches the
browser as ``/o/{slug}/theme.css`` and ``/o/{slug}/logo`` (``brand_assets``);
``app.web.branding`` builds the header from the organisation. The call page (ADR 0123) has its
own sibling, ``participant_call_page``, the only way here to its response policy.
"""

from pathlib import Path

from fastapi import Request
from starlette.responses import HTMLResponse

from app.domain.scheduling import Group
from app.web.branding import brand_view, branding_service
from app.web.locale import RequestLocale
from app.web.security_policy import PageKind
from app.web.templates import create_templates, render, render_call_page

templates = create_templates([Path(__file__).parent / "templates"])


async def _scope(
    request: Request, locale: RequestLocale, group: Group, context: dict[str, object]
) -> tuple[RequestLocale, dict[str, object]]:
    """Return the locale bound to ``group``'s organisation and the page's full context."""
    await locale.translator.refresh()
    organisation = await branding_service(request).for_group(group)
    return (
        locale.for_scope(group.organisation_id, group.id),
        {**context, "group": group, "brand": brand_view(organisation)},
    )


async def participant_page(
    request: Request,
    locale: RequestLocale,
    name: str,
    group: Group,
    context: dict[str, object],
    status: int = 200,
) -> HTMLResponse:
    """Render a participant page of ``group`` in its organisation's branding and wording.

    The organisation's own texts (``app.domain.text_overrides``) replace the shipped ones; a
    template's ``_()`` is bound to the group's organisation here, the one place all participant
    pages pass. The page may load its own scripts (``PageKind.SCRIPTED``).
    """
    scoped, full = await _scope(request, locale, group, context)
    response = render(templates, request, name, scoped, full, kind=PageKind.SCRIPTED)
    response.status_code = status
    return response


async def participant_call_page(
    request: Request,
    locale: RequestLocale,
    name: str,
    group: Group,
    context: dict[str, object],
) -> HTMLResponse:
    """Render the participant's call page (ADR 0123) with the call page's response policy.

    As ``participant_page`` otherwise. Only ``app.api.call_pages`` uses it.
    """
    scoped, full = await _scope(request, locale, group, context)
    return render_call_page(templates, request, name, scoped, full)
