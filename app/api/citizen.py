"""Shared plumbing of the citizen pages: templates, the organisation's branding, rendering.

Every citizen page of a group goes through ``citizen_page``, so each shows the logo, name and
accent colour of the group's organisation (WP3b) and may load the page's own scripts
(ADR 0020). The branding reaches the browser as ``/o/{slug}/theme.css`` and ``/o/{slug}/logo``
(``brand_assets``); ``app.web.branding`` builds the header from the organisation.
"""

from pathlib import Path

from fastapi import Request
from starlette.responses import HTMLResponse

from app.domain.scheduling import Group
from app.web.branding import brand_view, branding_service
from app.web.locale import RequestLocale
from app.web.templates import create_templates, render

templates = create_templates([Path(__file__).parent / "templates"])


async def citizen_page(
    request: Request,
    locale: RequestLocale,
    name: str,
    group: Group,
    context: dict[str, object],
    status: int = 200,
) -> HTMLResponse:
    """Render a citizen page of ``group`` in its organisation's branding."""
    organisation = await branding_service(request).for_group(group)
    response = render(
        templates,
        request,
        name,
        locale,
        {**context, "group": group, "brand": brand_view(organisation)},
        scripts=True,
    )
    response.status_code = status
    return response
