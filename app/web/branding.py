"""An organisation's branding as pages show it: header, theme stylesheet, logo and fonts.

Shared by the participant pages (``app.api.participant``), the branding assets
(``app.api.brand_assets``) and the admin preview (``app.admin.branding_routes``). The theme, logo and font URLs carry a
version that changes with the content, so browsers may cache them.

``theme_css`` writes only values ``app.domain.theme`` validated, inside fixed CSS of our own
(ADR 0041): no text an organisation typed reaches the stylesheet unchecked.
"""

from dataclasses import dataclass

from fastapi import Request

from app.domain.branding import FontRole
from app.domain.branding_service import BrandingService
from app.domain.scheduling import Organisation
from app.domain.theme import ACCENT_PROPERTY, PREFIX, SPECS, TokenKind

FONT_FAMILIES = {
    FontRole.TEXT: "OpenPlanner Text",
    FontRole.TEXT_BOLD: "OpenPlanner Text",
    FontRole.HEADING: "OpenPlanner Heading",
}
"""The family name each uploaded font is declared under; regular and bold share one."""

_FONT_WEIGHTS = {FontRole.TEXT: "400", FontRole.TEXT_BOLD: "700", FontRole.HEADING: "100 900"}


@dataclass(frozen=True)
class BrandView:
    """What a page header needs.

    Attributes:
        name: The name in the header.
        theme_href: The theme stylesheet, versioned.
        logo_href: The logo, versioned, if any.
        header_style: ``standard``, ``accent`` or ``logo`` (the class ``site-header--...``).
    """

    name: str
    theme_href: str
    logo_href: str | None
    header_style: str = "standard"


def brand_view(organisation: Organisation) -> BrandView:
    """Return the header of ``organisation``'s pages."""
    branding = organisation.branding
    logo = branding.logo
    return BrandView(
        name=branding.display_name or organisation.name,
        theme_href=f"/o/{organisation.slug}/theme.css?v={branding.theme_digest}",
        logo_href=f"/o/{organisation.slug}/logo?v={logo.digest}" if logo else None,
        header_style=branding.effective_header.value,
    )


def font_href(organisation: Organisation, role: FontRole, digest: str) -> str:
    """Return the versioned URL of an organisation's font."""
    return f"/o/{organisation.slug}/font/{role}?v={digest}"


def _declarations(pairs: list[tuple[str, str]], indent: str = "  ") -> str:
    """Return custom-property declarations, one per line."""
    return "".join(f"{indent}{PREFIX}{name}: {value};\n" for name, value in pairs)


def theme_css(organisation: Organisation) -> str:
    """Return the stylesheet that applies ``organisation``'s branding to ``site.css``.

    Colour tokens other than the accent sit in ``@media not (prefers-contrast: more)``, so a
    participant who asks for more contrast gets the defaults of ``site.css`` (ADR 0041 §4).
    """
    branding = organisation.branding
    theme = branding.theme
    parts: list[str] = []
    for font in branding.fonts:
        parts.append(
            "@font-face {\n"
            f'  font-family: "{FONT_FAMILIES[font.role]}";\n'
            f'  src: url("{font_href(organisation, font.role, font.digest)}") format("woff2");\n'
            f"  font-weight: {_FONT_WEIGHTS[font.role]};\n"
            "  font-display: swap;\n"
            "}\n"
        )
    tokens = [(ACCENT_PROPERTY, branding.accent_color)]
    colours: list[tuple[str, str]] = []
    for name, value in theme.values:
        if SPECS[name].kind is TokenKind.COLOUR:
            colours.append((name, value))
        elif SPECS[name].kind is not TokenKind.FONT_STACK:
            tokens.append((name, value))
    if branding.font(FontRole.TEXT) or branding.font(FontRole.TEXT_BOLD):
        tokens.append(
            ("font-family", f'"{FONT_FAMILIES[FontRole.TEXT]}", {theme.resolved("font-family")}')
        )
    elif theme.get("font-family"):
        tokens.append(("font-family", theme.resolved("font-family")))
    heading = theme.get("font-family-heading") or f"var({PREFIX}font-family)"
    if branding.font(FontRole.HEADING):
        tokens.append(("font-family-heading", f'"{FONT_FAMILIES[FontRole.HEADING]}", {heading}'))
    elif theme.get("font-family-heading"):
        tokens.append(("font-family-heading", heading))
    parts.append(":root {\n" + _declarations(sorted(tokens)) + "}\n")
    if colours:
        parts.append(
            "@media not (prefers-contrast: more) {\n  :root {\n"
            + _declarations(colours, "    ")
            + "  }\n}\n"
        )
    return "".join(parts)


def branding_service(request: Request) -> BrandingService:
    """Return the application's branding service."""
    service: BrandingService = request.app.state.branding_service
    return service
