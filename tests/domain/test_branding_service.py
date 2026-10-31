"""BrandingService: who may change an organisation's branding, and what is audited."""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.branding import Branding, FontFile, FontRole, HeaderStyle, Logo
from app.domain.branding_service import BrandingService
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Organisation
from app.domain.theme import Theme, ThemeFileError
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, make_group
from tests.support.principals import principal

SUPER = principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo"}
    state.organisations = {1: Organisation(1, "Demo-gemeente", "demo")}
    state.groups[1] = make_group(id=1, organisation_id=1, slug="demo", name="Demo")
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> BrandingService:
    clock = FixedClock(NOW)
    return BrandingService(uow, clock, AdminService(uow, clock))


async def test_without_branding_the_defaults_apply(service: BrandingService) -> None:
    group = make_group(id=1, organisation_id=1, slug="demo", name="Demo")
    organisation = await service.for_group(group)
    assert organisation.branding == Branding()
    assert organisation.name == "Demo-gemeente"


async def test_superuser_changes_name_and_colour_and_it_is_audited(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    changed = await service.update(SUPER, "demo", Branding("Gemeente Demo", "#154273"))

    assert changed == ["accent_color", "display_name"]
    assert uow.state.organisations[1].branding == Branding("Gemeente Demo", "#154273")
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation) == (
        "branding.manage",
        Outcome.SUCCESS,
        "demo",
    )
    assert event.detail == "accent_color,display_name"  # field names, never values


async def test_saving_the_same_branding_changes_nothing(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    assert await service.update(SUPER, "demo", Branding()) == []
    assert uow.state.audit == []


async def test_org_admin_may_not_change_branding_handover_section_6(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as error:
        await service.update(ORG_ADMIN, "demo", Branding("X"))
    assert error.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.audit[-1].outcome is Outcome.DENIED


async def test_unknown_organisation(service: BrandingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.update(SUPER, "nope", Branding())
    assert error.value.code is ErrorCode.ORGANISATION_NOT_FOUND


async def test_logo_upload_and_removal(service: BrandingService, uow: InMemoryUnitOfWork) -> None:
    logo = Logo.from_upload(PNG)
    await service.set_logo(SUPER, "demo", logo)

    assert uow.state.organisations[1].branding.logo == logo.ref
    assert await service.logo("demo") == logo
    assert uow.state.audit[-1].detail == "logo"

    await service.remove_logo(SUPER, "demo")
    assert uow.state.organisations[1].branding.logo is None
    assert await service.logo("demo") is None
    assert uow.state.audit[-1].detail == "logo.remove"


async def test_a_new_name_keeps_the_logo(service: BrandingService, uow: InMemoryUnitOfWork) -> None:
    await service.set_logo(SUPER, "demo", Logo.from_upload(PNG))
    await service.update(SUPER, "demo", Branding("Nieuw"))
    assert uow.state.organisations[1].branding.logo is not None


async def test_org_admin_may_not_upload_a_logo(service: BrandingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.set_logo(ORG_ADMIN, "demo", Logo.from_upload(PNG))
    assert error.value.code is ErrorCode.NOT_PERMITTED


async def test_public_lookup_by_slug(service: BrandingService) -> None:
    assert (await service.public("demo")).slug == "demo"
    with pytest.raises(DomainError) as error:
        await service.public("nope")
    assert error.value.code is ErrorCode.ORGANISATION_NOT_FOUND


WOFF2 = b"wOF2" + b"\x00" * 64


async def test_theme_and_header_are_saved_and_audited_by_name(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    theme = Theme.of({"radius": "0px", "color-text": "#0B0C0C"})
    branding = Branding(theme=theme, header_style=HeaderStyle.ACCENT)

    changed = await service.update(SUPER, "demo", branding)

    assert changed == ["header_style", "theme"]
    assert uow.state.organisations[1].branding.theme == theme
    assert uow.state.audit[-1].detail == "header_style,theme"


async def test_colours_without_contrast_are_refused_before_anything_is_stored(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as error:
        await service.update(SUPER, "demo", Branding(theme=Theme.of({"color-text": "#AAAAAA"})))
    assert error.value.field == "color_text"
    assert uow.state.organisations[1].branding == Branding()


async def test_a_theme_file_replaces_accent_and_tokens(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    await service.update(
        SUPER, "demo", Branding("Naam", "#154273", theme=Theme.of({"radius": "0px"}))
    )

    changed = await service.apply_theme_file(SUPER, "demo", b"--booking-line-height: 1.8;")

    branding = uow.state.organisations[1].branding
    assert changed == ["accent_color", "theme"]
    assert branding.accent_color == "#01689B"  # left out: back to the default
    assert branding.theme == Theme.of({"line-height": "1.8"})
    assert branding.display_name == "Naam"
    assert uow.state.audit[-1].detail == "theme_file"


async def test_a_theme_file_is_checked_for_contrast_and_syntax(service: BrandingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.apply_theme_file(SUPER, "demo", b"--booking-color-page: #000000;")
    assert error.value.field == "color_page"
    with pytest.raises(ThemeFileError):
        await service.apply_theme_file(SUPER, "demo", b"body { display: none }")


async def test_org_admin_may_not_upload_a_theme_file(service: BrandingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.apply_theme_file(ORG_ADMIN, "demo", b"--booking-radius: 0px;")
    assert error.value.code is ErrorCode.NOT_PERMITTED


async def test_fonts_are_stored_per_role_and_removed(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    font = FontFile.from_upload(FontRole.HEADING, WOFF2)
    await service.set_font(SUPER, "demo", font)

    assert uow.state.organisations[1].branding.font(FontRole.HEADING) == font.ref
    assert await service.font("demo", FontRole.HEADING) == font
    assert await service.font("demo", FontRole.TEXT) is None
    assert uow.state.audit[-1].detail == "font.heading"
    await service.update(SUPER, "demo", Branding("Nieuw"))
    assert uow.state.organisations[1].branding.fonts == (font.ref,)  # kept by update

    await service.remove_font(SUPER, "demo", FontRole.HEADING)
    assert uow.state.organisations[1].branding.fonts == ()
    assert uow.state.audit[-1].detail == "font.heading.remove"


async def test_org_admin_may_not_upload_a_font(service: BrandingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.set_font(ORG_ADMIN, "demo", FontFile.from_upload(FontRole.TEXT, WOFF2))
    assert error.value.code is ErrorCode.NOT_PERMITTED
