"""The image of a closure (ADR 0037 §3, CL-3): set, replaced, described, removed, made public.

Only the image of a visible closure is public: once the closure has ended it is no longer
served, and a group without a closure has none.
"""

from datetime import timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import ImageFile, ImageText
from app.domain.services import BookingService
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, OUTSIDER, two_organisations
from tests.support.closures import NOTICES
from tests.support.domain import NOW
from tests.support.images import jpeg, png

TEXT = ImageText({"nl": "Een gesloten deur", "en": "A closed door"})


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations(next_id=100))


async def _close(closures: ClosureService, *, hours: int | None = None) -> None:
    ends = NOW + timedelta(hours=hours) if hours else None
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, ends, NOTICES))


async def test_group_admin_sets_an_image_that_citizens_then_get(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _close(closures)
    image = ImageFile.from_upload(png())

    await closures.set_image(GROUP_ADMIN, "demo", TEXT, image)

    closure = (await closures.overview(GROUP_ADMIN, "demo")).closure
    assert closure is not None
    assert closure.image == image.ref(TEXT)
    group = uow.state.groups[1]
    assert await bookings.closure_image(group) == image
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.detail) == (
        "closure.manage",
        Outcome.SUCCESS,
        "closure.image.update,file:1",
    )


async def test_the_text_can_change_without_a_new_file(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _close(closures)
    image = ImageFile.from_upload(jpeg())
    await closures.set_image(GROUP_ADMIN, "demo", TEXT, image)

    decorative = ImageText(decorative=True)
    await closures.set_image(GROUP_ADMIN, "demo", decorative, None)

    closure = (await closures.overview(GROUP_ADMIN, "demo")).closure
    assert closure is not None
    assert closure.image == image.ref(decorative)
    assert await bookings.closure_image(uow.state.groups[1]) == image
    assert uow.state.audit[-1].detail == "closure.image.update,file:0"


async def test_a_text_without_any_image_is_refused(closures: ClosureService) -> None:
    await _close(closures)

    with pytest.raises(DomainError) as refused:
        await closures.set_image(GROUP_ADMIN, "demo", TEXT, None)

    assert (refused.value.code, refused.value.field) == (ErrorCode.INVALID_CONFIGURATION, "image")


async def test_without_a_closure_there_is_nothing_to_illustrate(
    closures: ClosureService,
) -> None:
    with pytest.raises(DomainError) as refused:
        await closures.set_image(GROUP_ADMIN, "demo", TEXT, ImageFile.from_upload(png()))

    assert refused.value.code is ErrorCode.CLOSURE_NOT_FOUND


async def test_removing_the_image_takes_it_off_the_page(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _close(closures)
    await closures.set_image(GROUP_ADMIN, "demo", TEXT, ImageFile.from_upload(png()))

    await closures.remove_image(GROUP_ADMIN, "demo")

    closure = (await closures.overview(GROUP_ADMIN, "demo")).closure
    assert closure is not None
    assert closure.image is None
    assert await bookings.closure_image(uow.state.groups[1]) is None
    assert uow.state.audit[-1].detail == "closure.image.remove"


async def test_the_image_of_an_ended_closure_is_no_longer_public(
    closures: ClosureService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    await _close(closures)
    await closures.set_image(GROUP_ADMIN, "demo", TEXT, ImageFile.from_upload(png()))

    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)

    assert await bookings.closure_image(uow.state.groups[1]) is None


async def test_an_update_of_the_closure_keeps_its_image(
    closures: ClosureService, uow: InMemoryUnitOfWork
) -> None:
    await _close(closures)
    image = ImageFile.from_upload(png())
    await closures.set_image(GROUP_ADMIN, "demo", TEXT, image)

    await closures.update(GROUP_ADMIN, "demo", None, NOTICES, notify=False)

    closure = (await closures.overview(GROUP_ADMIN, "demo")).closure
    assert closure is not None
    assert closure.image == image.ref(TEXT)


async def test_another_organisations_admin_cannot_set_or_remove_the_image(
    closures: ClosureService, uow: InMemoryUnitOfWork
) -> None:
    await _close(closures)

    with pytest.raises(DomainError) as refused:
        await closures.set_image(OUTSIDER, "demo", TEXT, ImageFile.from_upload(png()))
    assert refused.value.code is ErrorCode.NOT_PERMITTED
    with pytest.raises(DomainError):
        await closures.remove_image(OUTSIDER, "demo")
    assert uow.state.closure_images == {}
