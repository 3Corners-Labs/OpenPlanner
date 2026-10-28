"""Prepared closure scenarios (ADR 0037 §4, CL-4).

Scenarios belong to an organisation (``organisation.closure``) or to one group
(``closure.manage``). A group admin sees and uses the organisation's scenarios and the group's
own, and cannot change the organisation's. Starting a closure from a scenario copies its
content: editing the scenario afterwards never changes the running closure.
"""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import ImageFile, ImageText
from app.domain.scenario_service import ScenarioInput, ScenarioService
from app.domain.scenarios import MAX_NAME_LENGTH, ClosureScenario, ScenarioOwner
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, two_organisations
from tests.support.closures import NOTICES, confirmed_booking
from tests.support.domain import NOW, TEST_TOKEN_KEY, make_group
from tests.support.images import png

ORGANISATION = ScenarioOwner(organisation="demo")
GROUP = ScenarioOwner(group="demo")
STRIKE = ScenarioInput(
    "Staking",
    {"nl": "Wegens een staking zijn wij dicht.", "en": "Closed because of a strike."},
    {"nl": "Staking"},
)
OUTAGE = ScenarioInput("Storing", NOTICES, {"en": "Outage"})
BLOCK = ContactBlock((ContactOption.create(ContactKind.PHONE, "14 020"),))
TEXT = ImageText({"nl": "Gesloten", "en": "Closed"})


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = two_organisations(next_id=100)
    state.groups[3] = make_group(id=3, organisation_id=1, slug="demo-twee", name="Demo twee")
    return InMemoryUnitOfWork(state)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def scenarios(uow: InMemoryUnitOfWork, clock: FixedClock) -> ScenarioService:
    return ScenarioService(uow, clock, AdminService(uow, clock))


@pytest.fixture
def closures(uow: InMemoryUnitOfWork, clock: FixedClock) -> ClosureService:
    bookings = BookingService(uow, clock, TEST_TOKEN_KEY)
    return ClosureService(uow, clock, AdminService(uow, clock), bookings)


# --- the scenario itself --------------------------------------------------------------------


def test_a_scenario_needs_a_short_one_line_name_and_both_notices() -> None:
    scenario = ClosureScenario(None, 1, None, "  Storing ", NOTICES)
    assert scenario.name == "Storing"
    assert scenario.is_organisation

    for name in ("", "x" * (MAX_NAME_LENGTH + 1), "twee\nregels"):
        with pytest.raises(DomainError) as refused:
            ClosureScenario(None, 1, None, name, NOTICES)
        assert refused.value.field == "scenario_name"
    with pytest.raises(DomainError) as refused:
        ClosureScenario(None, 1, 1, "Storing", {"nl": "Dicht"})
    assert refused.value.field == "notice_en"
    with pytest.raises(DomainError) as refused:
        ClosureScenario(None, 1, 1, "Storing", NOTICES, {"nl": "a\nb"})
    assert refused.value.field == "heading_nl"


def test_an_owner_is_an_organisation_or_a_group() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ScenarioOwner()
    with pytest.raises(ValueError, match="exactly one"):
        ScenarioOwner(organisation="demo", group="demo")


# --- managing ----------------------------------------------------------------------------


async def test_organisation_admin_prepares_a_scenario_audited_without_texts(
    scenarios: ScenarioService, uow: InMemoryUnitOfWork
) -> None:
    created = await scenarios.save(ORG_ADMIN, ORGANISATION, STRIKE)

    assert (created.organisation_id, created.group_id, created.name) == (1, None, "Staking")
    listed = await scenarios.list(ORG_ADMIN, ORGANISATION)
    assert [s.name for s in listed.own] == ["Staking"]
    assert listed.organisation == []
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation, event.group, event.detail) == (
        "organisation.closure",
        Outcome.SUCCESS,
        "demo",
        None,
        f"closure.scenario.create,scenario:{created.id}",
    )


async def test_a_group_admin_sees_the_organisations_scenarios_and_its_own(
    scenarios: ScenarioService,
) -> None:
    await scenarios.save(ORG_ADMIN, ORGANISATION, STRIKE)
    own = await scenarios.save(GROUP_ADMIN, GROUP, OUTAGE)
    await scenarios.save(ORG_ADMIN, ScenarioOwner(group="demo-twee"), OUTAGE)
    await scenarios.save(OUTSIDER, ScenarioOwner(organisation="andere"), STRIKE)

    listed = await scenarios.list(GROUP_ADMIN, GROUP)

    assert [s.name for s in listed.organisation] == ["Staking"]
    assert [s.id for s in listed.own] == [own.id]
    assert own.group_id == 1


async def test_a_group_admin_cannot_change_an_organisation_scenario(
    scenarios: ScenarioService,
) -> None:
    strike = await scenarios.save(ORG_ADMIN, ORGANISATION, STRIKE)
    assert strike.id is not None

    # Reading it through the group is fine (to use it); changing it is not.
    assert (await scenarios.get(GROUP_ADMIN, GROUP, strike.id)).name == "Staking"
    for change in (
        scenarios.save(GROUP_ADMIN, GROUP, OUTAGE, strike.id),
        scenarios.save_contacts(GROUP_ADMIN, GROUP, strike.id, BLOCK),
        scenarios.delete(GROUP_ADMIN, GROUP, strike.id),
        scenarios.set_image(GROUP_ADMIN, GROUP, strike.id, TEXT, ImageFile.from_upload(png())),
        scenarios.remove_image(GROUP_ADMIN, GROUP, strike.id),
    ):
        with pytest.raises(DomainError) as refused:
            await change
        assert refused.value.code is ErrorCode.SCENARIO_NOT_FOUND
    # Through the organisation the matrix refuses the group admin.
    with pytest.raises(DomainError) as refused:
        await scenarios.save(GROUP_ADMIN, ORGANISATION, OUTAGE, strike.id)
    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert (await scenarios.get(ORG_ADMIN, ORGANISATION, strike.id)).name == "Staking"


async def test_another_groups_or_organisations_scenario_is_not_found(
    scenarios: ScenarioService,
) -> None:
    other_group = await scenarios.save(ORG_ADMIN, ScenarioOwner(group="demo-twee"), OUTAGE)
    other_org = await scenarios.save(OUTSIDER, ScenarioOwner(organisation="andere"), STRIKE)

    for scenario_id in (other_group.id or 0, other_org.id or 0, 999):
        with pytest.raises(DomainError) as refused:
            await scenarios.get(GROUP_ADMIN, GROUP, scenario_id)
        assert refused.value.code is ErrorCode.SCENARIO_NOT_FOUND
    for scenario_id in (other_group.id or 0, other_org.id or 0):
        with pytest.raises(DomainError) as refused:
            await scenarios.get(ORG_ADMIN, ORGANISATION, scenario_id)
        assert refused.value.code is ErrorCode.SCENARIO_NOT_FOUND


async def test_contacts_image_and_deletion(
    scenarios: ScenarioService, uow: InMemoryUnitOfWork
) -> None:
    own = await scenarios.save(GROUP_ADMIN, GROUP, OUTAGE)
    scenario_id = own.id or 0
    image = ImageFile.from_upload(png())

    await scenarios.save_contacts(GROUP_ADMIN, GROUP, scenario_id, BLOCK)
    await scenarios.set_image(GROUP_ADMIN, GROUP, scenario_id, TEXT, image)
    stored = await scenarios.get(GROUP_ADMIN, GROUP, scenario_id)
    assert stored.contacts == BLOCK
    assert stored.image == image.ref(TEXT)
    assert await scenarios.image(GROUP_ADMIN, GROUP, scenario_id) == image
    renamed = await scenarios.save(GROUP_ADMIN, GROUP, STRIKE, scenario_id)
    assert (renamed.name, renamed.contacts, renamed.image) == ("Staking", BLOCK, image.ref(TEXT))

    await scenarios.save_contacts(GROUP_ADMIN, GROUP, scenario_id, ContactBlock(()))
    await scenarios.remove_image(GROUP_ADMIN, GROUP, scenario_id)
    cleared = await scenarios.get(GROUP_ADMIN, GROUP, scenario_id)
    assert (cleared.contacts, cleared.image) == (None, None)

    await scenarios.delete(GROUP_ADMIN, GROUP, scenario_id)
    assert (await scenarios.list(GROUP_ADMIN, GROUP)).own == []
    assert scenario_id not in uow.state.scenario_images
    assert [e.detail for e in uow.state.audit] == [
        f"closure.scenario.create,scenario:{scenario_id}",
        f"closure.scenario.contacts,scenario:{scenario_id},phone:1",
        f"closure.scenario.image,scenario:{scenario_id},file:1",
        f"closure.scenario.update,scenario:{scenario_id}",
        f"closure.scenario.contacts,scenario:{scenario_id},remove",
        f"closure.scenario.image.remove,scenario:{scenario_id}",
        f"closure.scenario.delete,scenario:{scenario_id}",
    ]


async def test_another_organisations_admin_is_refused(scenarios: ScenarioService) -> None:
    with pytest.raises(DomainError) as refused:
        await scenarios.list(OUTSIDER, GROUP)
    assert refused.value.code is ErrorCode.NOT_PERMITTED


# --- starting a closure from a scenario -------------------------------------------------


async def test_a_closure_copies_the_scenario_and_later_edits_leave_it_alone(
    scenarios: ScenarioService, closures: ClosureService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.bookings[10] = confirmed_booking()
    strike = await scenarios.save(ORG_ADMIN, ORGANISATION, STRIKE)
    scenario_id = strike.id or 0
    image = ImageFile.from_upload(png())
    await scenarios.save_contacts(ORG_ADMIN, ORGANISATION, scenario_id, BLOCK)
    await scenarios.set_image(ORG_ADMIN, ORGANISATION, scenario_id, TEXT, image)

    # The form starts from the scenario's texts; the admin may adjust them before confirming.
    adjusted = {**STRIKE.notices, "nl": "Wegens een staking zijn wij vandaag dicht."}
    start = ClosureInput(None, None, adjusted, STRIKE.headings, scenario_id=scenario_id)
    count = await closures.close_group(GROUP_ADMIN, "demo", start)
    started = uow.state.audit[-1]

    await scenarios.save(ORG_ADMIN, ORGANISATION, OUTAGE, scenario_id)
    await scenarios.save_contacts(ORG_ADMIN, ORGANISATION, scenario_id, ContactBlock(()))
    await scenarios.remove_image(ORG_ADMIN, ORGANISATION, scenario_id)

    closure = (await closures.overview(GROUP_ADMIN, "demo")).closure
    assert closure is not None
    assert closure.notice("nl") == "Wegens een staking zijn wij vandaag dicht."
    assert closure.heading("nl") == "Staking"
    assert closure.contacts == BLOCK
    assert closure.image == image.ref(TEXT)
    assert uow.state.closure_images[closure.id or 0] == image
    assert (started.action, started.detail) == (
        "closure.manage",
        f"closure.start,scenario:{scenario_id},bookings:1,contacts:1",
    )
    assert count == 1


async def test_a_scenario_without_contacts_takes_the_block_that_applies(
    scenarios: ScenarioService, closures: ClosureService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.contact_blocks[1] = BLOCK
    own = await scenarios.save(GROUP_ADMIN, GROUP, OUTAGE)

    await closures.close_group(
        GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES, scenario_id=own.id)
    )

    closure = (await closures.overview(GROUP_ADMIN, "demo")).closure
    assert closure is not None
    assert closure.contacts == BLOCK
    assert closure.image is None


async def test_a_closure_cannot_use_another_groups_scenario(
    scenarios: ScenarioService, closures: ClosureService
) -> None:
    other = await scenarios.save(ORG_ADMIN, ScenarioOwner(group="demo-twee"), OUTAGE)
    foreign = await scenarios.save(OUTSIDER, ScenarioOwner(organisation="andere"), STRIKE)

    for scenario_id in (other.id, foreign.id, 999):
        with pytest.raises(DomainError) as refused:
            await closures.close_group(
                GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES, scenario_id=scenario_id)
            )
        assert refused.value.code is ErrorCode.SCENARIO_NOT_FOUND
    assert (await closures.overview(GROUP_ADMIN, "demo")).closure is None


async def test_closing_the_organisation_uses_one_organisation_scenario_for_every_group(
    scenarios: ScenarioService, closures: ClosureService
) -> None:
    strike = await scenarios.save(ORG_ADMIN, ORGANISATION, STRIKE)
    image = ImageFile.from_upload(png())
    await scenarios.set_image(ORG_ADMIN, ORGANISATION, strike.id or 0, TEXT, image)
    group_own = await scenarios.save(GROUP_ADMIN, GROUP, OUTAGE)

    with pytest.raises(DomainError) as refused:
        await closures.close_organisation(
            ORG_ADMIN, "demo", ClosureInput(None, None, NOTICES, scenario_id=group_own.id)
        )
    assert refused.value.code is ErrorCode.SCENARIO_NOT_FOUND

    outcomes = await closures.close_organisation(
        ORG_ADMIN, "demo", ClosureInput(None, None, STRIKE.notices, scenario_id=strike.id)
    )

    assert sorted(o.slug for o in outcomes if o.changed) == ["demo", "demo-twee"]
    for slug in ("demo", "demo-twee"):
        closure = (await closures.overview(ORG_ADMIN, slug)).closure
        assert closure is not None
        assert closure.image == image.ref(TEXT)
    # Each group got its own copy: removing one leaves the other.
    await closures.remove_image(ORG_ADMIN, "demo")
    other = (await closures.overview(ORG_ADMIN, "demo-twee")).closure
    assert other is not None
    assert other.image == image.ref(TEXT)
