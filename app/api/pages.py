"""Public server-rendered pages. The group booking page ``/g/{slug}`` joins in WP3."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.web.locale import RequestLocale, request_locale
from app.web.templates import create_templates, render

router = APIRouter(include_in_schema=False)
templates = create_templates([Path(__file__).parent / "templates"])


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request, locale: Annotated[RequestLocale, Depends(request_locale)]
) -> HTMLResponse:
    """Explain the service; visitors normally arrive on a group page via their organisation."""
    return render(templates, request, "home.html", locale)
