"""The time zone and staffing of a counted pool in a configuration document (ADR 0122 step 3)."""

from typing import Any

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import ChangeAction
from app.domain.configuration_v1_pool_staffing import check_staffing
from app.domain.errors import DomainError, ErrorCode
from app.domain.pool_staffing import MAX_POOL_OVERRIDES, MAX_POOL_RULES
from tests.support.configuration import Recorder
from tests.support.flow_documents import (
    acme,
    document_json,
    empty_flows,
    full,
    plan,
    pool,
    refusals,
)

LOKET = "organisations[acme].capacity_pools[loket]"
MORNING: dict[str, Any] = {
    "weekday": 0,
    "opens": "09:00",
    "closes": "12:00",
    "units": 3,
    "label": None,
    "offer": "scheduled",
    "valid_from": None,
    "valid_to": None,
}
CLOSED: dict[str, Any] = {
    "day": "2026-12-25",
    "opens": None,
    "closes": None,
    "units": None,
    "label": None,
    "offer": "scheduled",
}


def staffed(**fields: Any) -> Any:
    """Return the full installation with the pool ``loket`` staffed as given."""
    values: dict[str, Any] = {
        "timezone": "Europe/Amsterdam",
        "staffing_rules": [MORNING],
        "staffing_overrides": [CLOSED],
        **fields,
    }
    current = full()
    organisation = current.children["organisations"][0]
    pools = (pool("loket", "passport", **values),)
    organisation.children["capacity_pools"] = pools
    return current


def loket(data: dict[str, Any]) -> dict[str, Any]:
    """Return the pool ``loket`` of the first organisation of a document."""
    found: dict[str, Any] = acme(data)["capacity_pools"][0]
    return found


def test_an_export_writes_the_staffing_and_importing_it_again_plans_nothing() -> None:
    current = staffed()
    data = document_json(current)

    assert loket(data)["timezone"] == "Europe/Amsterdam"
    assert loket(data)["staffing_rules"] == [MORNING]
    assert loket(data)["staffing_overrides"] == [CLOSED]
    result = plan(data, current)
    assert result.ok
    assert result.changes == ()


def test_the_same_staffing_is_the_same_document_however_it_is_listed() -> None:
    afternoon = {**MORNING, "opens": "13:00", "closes": "17:00"}
    tuesday = {**MORNING, "weekday": 1}
    current = staffed(staffing_rules=[MORNING, afternoon, tuesday])
    data = document_json(current)
    loket(data)["staffing_rules"] = [tuesday, afternoon, MORNING]

    assert plan(data, current).changes == ()
    assert check_staffing(loket(data))["staffing_rules"] == [MORNING, afternoon, tuesday]


def test_a_document_without_the_fields_leaves_the_staffing_as_it_is() -> None:
    current = staffed()
    data = document_json(current)
    for name in ("timezone", "staffing_rules", "staffing_overrides"):
        loket(data).pop(name)
    loket(data)["name"] = "Loket 2"

    result = plan(data, current)

    (change,) = result.changes
    assert change.fields == ("name",)
    assert result.ok


def test_an_old_document_creates_a_pool_without_staffing() -> None:
    data = document_json(empty_flows())
    acme(data)["capacity_pools"] = [{"code": "loket", "name": "Loket", "guaranteed_skills": []}]

    result = plan(data, empty_flows())

    assert result.ok
    (change,) = [c for c in result.changes if c.kind == "capacity_pool"]
    assert change.action is ChangeAction.CREATE
    assert change.values["timezone"] is None
    assert change.values["staffing_rules"] == []


def test_a_staffing_change_needs_the_capacity_right_on_the_organisation() -> None:
    current = staffed()
    data = document_json(current)
    loket(data)["staffing_rules"] = [{**MORNING, "units": 2}]
    recorder = Recorder(Permission.CAPACITY_MANAGE)

    result = plan(data, current, may_change=recorder)

    assert not result.ok
    assert (Permission.CAPACITY_MANAGE, Target.for_organisation("acme")) in recorder.asked
    assert plan(data, current).ok


def test_lowering_the_units_below_what_is_booked_is_not_refused() -> None:
    current = staffed()
    data = document_json(current)
    loket(data)["staffing_rules"] = [{**MORNING, "units": 0}]
    loket(data)["staffing_overrides"] = []

    result = plan(data, current)

    assert result.ok
    (change,) = result.changes
    assert set(change.fields) == {"staffing_rules", "staffing_overrides"}


def test_an_exception_in_the_past_is_read_as_recorded() -> None:
    current = staffed()
    data = document_json(current)
    loket(data)["staffing_overrides"] = [{**CLOSED, "day": "2020-01-01"}]

    assert plan(data, current).ok


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"timezone": None}, "timezone"),
        (
            {"staffing_rules": [MORNING, {**MORNING, "opens": "11:00", "closes": "13:00"}]},
            "staffing_rules",
        ),
        (
            {
                "staffing_overrides": [
                    CLOSED,
                    {**CLOSED, "opens": "09:00", "closes": "10:00", "units": 1},
                ]
            },
            "staffing_overrides",
        ),
        ({"staffing_rules": [{**MORNING, "opens": "25:00"}]}, "opens"),
        ({"staffing_rules": [{**MORNING, "opens": "13:00"}]}, "window"),
        ({"staffing_rules": [{**MORNING, "valid_from": "soon"}]}, "valid_from"),
        ({"staffing_overrides": [{**CLOSED, "day": "xmas"}]}, "day"),
        ({"staffing_overrides": [{**CLOSED, "opens": "09:00"}]}, "window"),
    ],
)
def test_a_staffing_the_portal_would_refuse_is_refused_naming_the_field(
    changes: dict[str, Any], field: str
) -> None:
    current = staffed()
    data = document_json(current)
    loket(data).update(changes)

    result = plan(data, current)

    assert refusals(result) == [(LOKET, ErrorCode.INVALID_CONFIGURATION, field)]


@pytest.mark.parametrize(
    "changes",
    [
        {"staffing_rules": [{**MORNING, "units": 1001}]},
        {"staffing_rules": [{**MORNING, "units": -1}]},
        {"staffing_rules": [{**MORNING, "weekday": 7}]},
        {"staffing_rules": [{**MORNING, "offer": "sometimes"}]},
        {"staffing_rules": [{**MORNING, "subjects": ["paspoort"]}]},
        {"staffing_overrides": [{**CLOSED, "subjects": []}]},
    ],
)
def test_what_the_schema_of_an_item_forbids_is_refused(changes: dict[str, Any]) -> None:
    current = staffed()
    data = document_json(current)
    loket(data).update(changes)

    with pytest.raises(DomainError):
        plan(data, current)


@pytest.mark.parametrize("offer", ["scheduled", "direct", "both"])
def test_a_block_may_offer_appointments_without_appointment_or_both(offer: str) -> None:
    current = staffed()
    data = document_json(current)
    loket(data)["staffing_rules"] = [{**MORNING, "offer": offer}]

    assert plan(data, current).ok


def test_the_number_of_blocks_and_exceptions_is_bounded() -> None:
    many_rules = [
        {**MORNING, "weekday": n % 7, "opens": f"{n // 7 % 24:02d}:00", "closes": "23:59"}
        for n in range(MAX_POOL_RULES + 1)
    ]
    days = [{**CLOSED, "day": f"{2027 + n // 365}-01-01"} for n in range(MAX_POOL_OVERRIDES + 1)]

    with pytest.raises(DomainError) as rules:
        check_staffing({"timezone": "UTC", "staffing_rules": many_rules})
    with pytest.raises(DomainError) as overrides:
        check_staffing({"timezone": "UTC", "staffing_overrides": days})

    assert (rules.value.code, rules.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "staffing_rules",
    )
    assert overrides.value.field == "staffing_overrides"


def test_a_refusal_names_the_field_and_never_a_value() -> None:
    current = staffed()
    data = document_json(current)
    loket(data)["timezone"] = "Secret/Place"

    result = plan(data, current)

    assert "Secret/Place" not in repr(result.refusals)
    assert "Secret/Place" not in repr(result.changes)
