"""A whole document in the version-1 format: read, planned, written back (ADR 0099)."""

import copy
from typing import Any

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import RuleContext, Usage
from app.domain.configuration_document import Document, Mode, parse_document, to_json
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS
from app.domain.errors import ErrorCode
from tests.support.configuration import Recorder, allow
from tests.support.configuration_parts import opened

DOCUMENT: dict[str, Any] = {
    "format": "openplanner.config/v1",
    "platform": {
        "settings": {"rate_limit_bookings": "5/hour"},
        "api": {"enabled": True},
        "holiday_calendars": [{"slug": "nl", "name": "Nederland", "days": {"2026-12-25": "Kerst"}}],
    },
    "organisations": [
        {
            "slug": "acme",
            "name": "Acme",
            "default_locale": "nl",
            "holiday_calendars": ["nl"],
            "settings": {"time_format": "24h"},
            "email": {
                "host": "smtp.voorbeeld.nl",
                "sender_address": "afspraken@voorbeeld.nl",
                "secret": {"sealed": "c2VjcmV0"},
            },
            "groups": [
                {
                    "slug": "helpdesk",
                    "name": "Helpdesk",
                    "timezone": "Europe/Amsterdam",
                    "slot_minutes": 30,
                    "min_notice_minutes": 60,
                    "horizon_days": 60,
                    "phone_mode": "optional",
                    "schedule_rules": [
                        {"weekday": 0, "opens": "09:00", "closes": "17:00", "capacity": 3}
                    ],
                    "reminder_rules": [{"channel": "email", "minutes_before": 1440}],
                    "texts": {"nl": {"message.signoff": "Tot ziens, {group_name}"}},
                }
            ],
        }
    ],
}


def current() -> Entity:
    return Entity("installation", (), children={"platform": (Entity("platform"),)})


def plan(data: dict[str, Any], *, may_change: Any = allow, usage: Usage | None = None) -> Plan:
    document = opened(parse_document(data, FORMATS))
    return plan_document(
        document,
        current(),
        usage=usage or Usage(),
        may_change=may_change,
        context=RuleContext(),
        settings_version=1,
    )


def test_a_document_across_the_aggregates_plans_without_refusal() -> None:
    result = plan(DOCUMENT)

    assert result.ok, result.refusals
    kinds = [change.kind for change in result.changes]
    for expected in (
        "platform_setting",
        "api_settings",
        "holiday_calendar",
        "holiday",
        "organisation",
        "organisation_setting",
        "email_channel",
        "group",
    ):
        assert expected in kinds
    assert result.lines()[0] == "create platform_setting rate_limit_bookings: value"


def test_the_document_is_written_back_in_a_form_that_reads_back_the_same() -> None:
    document = parse_document(DOCUMENT, FORMATS)

    written = to_json(document)

    # the items of a list are written with their defaults filled in; nothing else is added
    assert written["organisations"][0]["groups"][0]["schedule_rules"][0]["offer"] == "scheduled"
    assert to_json(parse_document(written, FORMATS)) == written
    assert isinstance(document, Document)
    assert document.mode is Mode.MERGE


def test_an_organisation_document_cannot_reach_the_platform() -> None:
    data = {**DOCUMENT, "scope": {"organisation": "acme"}}

    result = plan(data)

    assert ("platform", ErrorCode.CONFIGURATION_OUT_OF_SCOPE) in [
        (r.path, r.code) for r in result.refusals
    ]


def test_each_part_asks_for_the_permission_of_its_own_screen() -> None:
    recorder = Recorder()

    plan(DOCUMENT, may_change=recorder)

    asked = set(recorder.asked)
    assert (Permission.SETTINGS_PLATFORM_MANAGE, Target.everything()) in asked
    assert (Permission.HOLIDAYS_MANAGE, Target.everything()) in asked
    assert (Permission.ORGANISATION_MANAGE, Target.everything()) in asked
    assert (Permission.SETTINGS_ORGANISATION_MANAGE, Target.for_organisation("acme")) in asked
    assert (Permission.INTEGRATIONS_MANAGE, Target.for_organisation("acme")) in asked
    assert (Permission.GROUP_CREATE, Target.for_organisation("acme")) in asked
    assert (Permission.SCHEDULE_MANAGE, Target.for_group("helpdesk", "acme")) in asked
    assert (Permission.REMINDERS_MANAGE, Target.for_group("helpdesk", "acme")) in asked


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("organisations", 0, "groups", 0, "slot_minutes"), 7, ErrorCode.INVALID_CONFIGURATION),
        (
            ("organisations", 0, "groups", 0, "timezone"),
            "Mars/Olympus",
            ErrorCode.INVALID_CONFIGURATION,
        ),
        (("organisations", 0, "default_locale"), "xx", ErrorCode.INVALID_CONFIGURATION),
        (
            ("organisations", 0, "holiday_calendars"),
            ["onbekend"],
            ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND,
        ),
        (("platform", "settings", "rate_limit_bookings"), "500/hour", ErrorCode.SETTING_INVALID),
    ],
)
def test_what_the_portal_would_refuse_is_refused(
    path: tuple[str | int, ...], value: Any, code: ErrorCode
) -> None:
    data = copy.deepcopy(DOCUMENT)
    node: Any = data
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value

    result = plan(data)

    assert code in {refusal.code for refusal in result.refusals}
