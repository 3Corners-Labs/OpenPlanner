"""Seed skills, capacity pools and people for the HTTP tests of ADR 0121 through the services.

The route tests of the skill catalogue, the capacity pools and the skills of a person stand on
what these helpers store, not on forms posted and ids read back from rendered pages. The
services are the application's own (``app.state``), so every rule and audit event applies, as
the superuser.
"""

from app.domain.capacity_service import CapacityService
from app.domain.skill_service import SkillService
from tests.admin.conftest import SUPER, Site
from tests.support.admin_site import site_app, site_uow


async def add_skill(
    site: Site,
    code: str = "intake",
    nl: str = "Intake",
    en: str = "Intake (en)",
    organisation: str = "demo",
) -> int:
    """Add a skill to ``organisation`` and return its id."""
    skill = await site_app(site).state.skill_service.create(SUPER, organisation, code, nl, en)
    assert skill.id is not None
    return int(skill.id)


async def archive_skill(site: Site, skill_id: int, organisation: str = "demo") -> None:
    """Archive a skill of ``organisation``."""
    await site_app(site).state.skill_service.archive(SUPER, organisation, skill_id)


async def add_pool(
    site: Site,
    name: str = "Loket",
    skills: tuple[int, ...] = (),
    organisation: str = "demo",
) -> int:
    """Add a counted pool to ``organisation``, guaranteeing ``skills``, and return its id."""
    pool = await site_app(site).state.capacity_service.create(SUPER, organisation, name, skills)
    assert pool.id is not None
    return int(pool.id)


async def archive_pool(site: Site, pool_id: int, organisation: str = "demo") -> None:
    """Archive a counted pool of ``organisation``."""
    await site_app(site).state.capacity_service.archive(SUPER, organisation, pool_id)


async def staff_id(site: Site, subject: str) -> int:
    """Return the number of the person with login subject ``subject`` (who has logged in)."""
    async with site_uow(site).read() as tx:
        member = await tx.get_staff(subject)
    assert member is not None
    assert member.id is not None
    return int(member.id)


def limit(site: Site, skills: int | None = None, pools: int | None = None) -> None:
    """Replace the application's services by ones with a smaller limit (the live setting is
    bounded below at 10 skills and 5 pools, which would take many requests to reach)."""
    app = site_app(site)
    state = app.state
    if skills is not None:
        state.skill_service = SkillService(site_uow(site), state.clock, state.admin_service, skills)
    if pools is not None:
        state.capacity_service = CapacityService(
            site_uow(site), state.clock, state.admin_service, pools
        )
