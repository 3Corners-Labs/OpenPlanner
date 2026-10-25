"""An organisation's branding as pages show it: header name, theme stylesheet and logo.

Shared by the citizen pages (``app.api.citizen``) and the admin preview
(``app.admin.branding_routes``). The theme and logo URLs carry a version that changes with
the content (``app.api.brand_assets``), so browsers may cache them.
"""

from dataclasses import dataclass

from fastapi import Request

from app.domain.branding_service import BrandingService
from app.domain.scheduling import Organisation


@dataclass(frozen=True)
class BrandView:
    """What a page header needs: the name, the theme stylesheet and the logo, if any."""

    name: str
    theme_href: str
    logo_href: str | None


def brand_view(organisation: Organisation) -> BrandView:
    """Return the header of ``organisation``'s pages."""
    branding = organisation.branding
    logo = branding.logo
    return BrandView(
        name=branding.display_name or organisation.name,
        theme_href=f"/o/{organisation.slug}/theme.css?v={branding.accent_color[1:]}",
        logo_href=f"/o/{organisation.slug}/logo?v={logo.digest}" if logo else None,
    )


def branding_service(request: Request) -> BrandingService:
    """Return the application's branding service."""
    service: BrandingService = request.app.state.branding_service
    return service
