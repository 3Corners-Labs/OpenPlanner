"""Branding assets of an organisation: the theme stylesheet, the logo and fonts.

    GET /o/{slug}/theme.css      CSS custom properties from the organisation's branding
    GET /o/{slug}/logo           the logo image (PNG, JPEG or WebP), 404 without one
    GET /o/{slug}/font/{role}    an uploaded WOFF2 font (ADR 0041), 404 without one

The stylesheet is generated from values the domain validated (``app.web.branding.theme_css``),
so no other text can reach it. Pages link all three with a version parameter that changes
with the content, so the responses may be cached by browsers.
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.domain.branding import FontRole
from app.web.branding import branding_service, theme_css
from app.web.protection import asset_limit, limiter

router = APIRouter(include_in_schema=False)

CACHE = "public, max-age=3600"


@router.get("/o/{slug}/theme.css")
@limiter.limit(asset_limit)
async def theme(request: Request, slug: str) -> Response:
    """Serve the organisation's theme stylesheet."""
    organisation = await branding_service(request).public(slug)
    return Response(
        theme_css(organisation), media_type="text/css", headers={"cache-control": CACHE}
    )


@router.get("/o/{slug}/logo")
@limiter.limit(asset_limit)
async def logo(request: Request, slug: str) -> Response:
    """Serve the organisation's logo, or 404."""
    found = await branding_service(request).logo(slug)
    if found is None:
        return Response(status_code=404)
    return Response(found.data, media_type=found.media_type, headers={"cache-control": CACHE})


@router.get("/o/{slug}/font/{role}")
@limiter.limit(asset_limit)
async def font(request: Request, slug: str, role: str) -> Response:
    """Serve one of the organisation's fonts, or 404 (also for an unknown role)."""
    if role not in set(FontRole):
        return Response(status_code=404)
    found = await branding_service(request).font(slug, FontRole(role))
    if found is None:
        return Response(status_code=404)
    return Response(found.data, media_type="font/woff2", headers={"cache-control": CACHE})
