"""Branding assets of an organisation: the theme stylesheet and the logo (WP3b).

    GET /o/{slug}/theme.css   CSS custom properties from the organisation's branding
    GET /o/{slug}/logo        the logo image (PNG, JPEG or WebP), 404 without one

The stylesheet is generated from a colour the domain validated as ``#RRGGBB`` (``Branding``),
so no other text can reach it. Pages link both with a version parameter that changes with
the content, so the responses may be cached by browsers.
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.domain.branding import Branding
from app.web.branding import branding_service
from app.web.protection import asset_limit, limiter

router = APIRouter(include_in_schema=False)

CACHE = "public, max-age=3600"


def theme_css(branding: Branding) -> str:
    """Return the stylesheet that applies ``branding`` to ``site.css``."""
    return f":root {{\n  --booking-color-accent: {branding.accent_color};\n}}\n"


@router.get("/o/{slug}/theme.css")
@limiter.limit(asset_limit)
async def theme(request: Request, slug: str) -> Response:
    """Serve the organisation's theme stylesheet."""
    organisation = await branding_service(request).public(slug)
    return Response(
        theme_css(organisation.branding),
        media_type="text/css",
        headers={"cache-control": CACHE},
    )


@router.get("/o/{slug}/logo")
@limiter.limit(asset_limit)
async def logo(request: Request, slug: str) -> Response:
    """Serve the organisation's logo, or 404."""
    found = await branding_service(request).logo(slug)
    if found is None:
        return Response(status_code=404)
    return Response(found.data, media_type=found.media_type, headers={"cache-control": CACHE})
