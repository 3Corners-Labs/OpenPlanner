"""Alert targets in the version-1 document: their key, their checks and their limit."""

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import ChangeAction
from app.domain.configuration_document import to_json
from app.domain.configuration_schema import Collection, Kind, Shape
from app.domain.configuration_v1_alerts import (
    INTEGRITY,
    ORGANISATION_ALERT_TARGETS,
    PLATFORM_ALERT_TARGETS,
)
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import Recorder, entity, parse
from tests.support.configuration_columns import uncovered_columns
from tests.support.configuration_parts import installation, organisation, plan_opened, stub_format

FORMAT = stub_format((ORGANISATION_ALERT_TARGETS,), (), *INTEGRITY)
PLATFORM_FORMAT = None


def platform_format():
    platform = Kind(
        "platform",
        permission=Permission.SETTINGS_PLATFORM_MANAGE,
        deletable=False,
        collections=(PLATFORM_ALERT_TARGETS,),
    )
    root = Kind("installation", collections=(Collection("platform", platform, Shape.SINGLE),))
    return type(FORMAT)("test/v1", root, integrity=INTEGRITY)


def test_a_target_is_created_in_the_organisation_with_the_integrations_permission() -> None:
    recorder = Recorder()
    data = {
        "organisations": [
            {"slug": "acme", "alert_targets": {"email": {"ops@voorbeeld.nl": "outage"}}}
        ]
    }

    result = plan_opened(data, installation(organisation()), FORMAT, may_change=recorder)

    (change,) = result.changes
    assert (change.kind, change.address, change.action) == (
        "organisation_alert_target",
        ("acme", "email", "ops@voorbeeld.nl"),
        ChangeAction.CREATE,
    )
    assert change.values["min_level"] == "outage"
    assert (Permission.INTEGRATIONS_MANAGE, Target.for_organisation("acme")) in recorder.asked
    assert result.ok


def test_the_level_defaults_to_warning_and_is_a_change_when_it_differs() -> None:
    current = installation(
        organisation(
            alert_targets=(
                entity(
                    "organisation_alert_target",
                    ("email", "ops@voorbeeld.nl"),
                    {"min_level": "warning"},
                ),
            )
        )
    )

    same = plan_opened(
        {
            "organisations": [
                {"slug": "acme", "alert_targets": {"email": {"ops@voorbeeld.nl": "warning"}}}
            ]
        },
        current,
        FORMAT,
    )
    other = plan_opened(
        {
            "organisations": [
                {"slug": "acme", "alert_targets": {"email": {"ops@voorbeeld.nl": "outage"}}}
            ]
        },
        current,
        FORMAT,
    )

    assert same.changes == ()
    assert [c.action for c in other.changes] == [ChangeAction.UPDATE]


@pytest.mark.parametrize(
    "data",
    [
        {"email": {"not an address": "warning"}},
        {"webex_space": {"short": "warning"}},
    ],
)
def test_an_address_that_does_not_fit_its_kind_is_refused(data: dict[str, object]) -> None:
    result = plan_opened(
        {"organisations": [{"slug": "acme", "alert_targets": data}]},
        installation(organisation()),
        FORMAT,
    )

    assert [(r.code, r.field) for r in result.refusals] == [
        (ErrorCode.INVALID_CONFIGURATION, "address")
    ]


def test_an_unknown_kind_or_level_is_refused_when_the_document_is_read() -> None:
    for data in ({"sms": {"x@voorbeeld.nl": "warning"}}, {"email": {"x@voorbeeld.nl": "alarm"}}):
        with pytest.raises(DomainError):
            parse({"organisations": [{"slug": "acme", "alert_targets": data}]}, FORMAT)


def test_an_owner_holds_at_most_ten_targets() -> None:
    many = {f"ops{n}@voorbeeld.nl": "warning" for n in range(11)}
    few = dict(list(many.items())[:10])

    refused = plan_opened(
        {"organisations": [{"slug": "acme", "alert_targets": {"email": many}}]},
        installation(organisation()),
        FORMAT,
    )
    fine = plan_opened(
        {"organisations": [{"slug": "acme", "alert_targets": {"email": few}}]},
        installation(organisation()),
        FORMAT,
    )

    assert [(r.path, r.field) for r in refused.refusals] == [
        ("organisations[acme].alert_targets", "targets")
    ]
    assert fine.ok


def test_the_platform_has_targets_of_its_own() -> None:
    fmt = platform_format()
    current = entity("installation", (), {}, {"platform": (entity("platform"),)})

    result = plan_opened(
        {"platform": {"alert_targets": {"email": {"ops@voorbeeld.nl": "warning"}}}}, current, fmt
    )

    (change,) = result.changes
    assert change.kind == "platform_alert_target"
    assert change.permissions == {Permission.SETTINGS_PLATFORM_MANAGE}
    many = {f"o{n}@voorbeeld.nl": "warning" for n in range(11)}
    refused = plan_opened({"platform": {"alert_targets": {"email": many}}}, current, fmt)
    assert [r.path for r in refused.refusals] == ["platform.alert_targets"]


def test_a_document_round_trips() -> None:
    data = {
        "format": "test/v1",
        "organisations": [
            {"slug": "acme", "alert_targets": {"email": {"a@voorbeeld.nl": "outage"}}}
        ],
    }

    assert to_json(parse({k: v for k, v in data.items() if k != "format"}, FORMAT)) == data


def test_every_column_of_the_table_is_covered() -> None:
    kinds = [PLATFORM_ALERT_TARGETS.kind, ORGANISATION_ALERT_TARGETS.kind]

    assert uncovered_columns(kinds) == {}
