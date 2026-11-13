"""The request tables keep personal data in one place under the one list (ADR 0121 step 5).

Decision D1: only ``request_personal_data`` holds personal data of a request, with exactly the
columns of ``privacy.REQUEST_DATA_FIELDS``; ``service_requests`` and ``work_items`` hold none
but the agent pseudonym of ``REQUEST_DERIVED_FIELDS``. The partial index of the one active work
item and the domain's set of active states are the same list.
"""

from app.adapters.db.models import (
    ACTIVE_WORK_ITEM,
    RequestPersonalDataRow,
    ServiceRequestRow,
    WorkItemRow,
)
from app.domain.privacy import ALIASES, PII_FIELDS, REQUEST_DATA_FIELDS, REQUEST_DERIVED_FIELDS
from app.domain.service_requests import ACTIVE_ITEM


def columns(row: type) -> set[str]:
    """Return the column names of a mapped table."""
    return {column.name for column in row.__table__.columns}  # type: ignore[attr-defined]


def test_the_record_has_exactly_the_personal_columns_of_the_list() -> None:
    assert columns(RequestPersonalDataRow) - {"request_id"} == set(REQUEST_DATA_FIELDS)


def test_requests_and_work_items_hold_no_personal_data_but_the_pseudonym() -> None:
    personal = PII_FIELDS | ALIASES
    assert not columns(ServiceRequestRow) & personal
    assert not columns(WorkItemRow) & personal
    assert set(REQUEST_DERIVED_FIELDS) <= columns(WorkItemRow)


def test_the_active_index_names_the_active_states_of_the_domain() -> None:
    listed = ACTIVE_WORK_ITEM.removeprefix("state IN (").removesuffix(")")
    assert {value.strip(" '") for value in listed.split(",")} == {s.value for s in ACTIVE_ITEM}
