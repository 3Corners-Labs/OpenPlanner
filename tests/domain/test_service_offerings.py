"""A service offering is the group under its business name (ADR 0121 points 1 and 8)."""

from app.domain.scheduling import Group
from app.domain.service_offerings import ServiceOffering, from_group


def test_a_service_offering_is_made_from_the_group_without_a_second_identity() -> None:
    group = Group(
        id=7,
        organisation_id=3,
        name="Balie",
        slug="balie",
        timezone="Europe/Amsterdam",
        slot_minutes=20,
    )
    assert from_group(group) == ServiceOffering(7, 3, "balie", "Balie")
