"""The staff directory's rules: who sees whom, filters and change events (ADR 0046)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.audit import Outcome
from app.domain.authorization import Role, Scope
from app.domain.staff import (
    CHANGED_EVENT,
    CREATED_EVENT,
    Membership,
    StaffFilter,
    StaffMember,
    change_events,
    visible,
)

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
DEMO = Membership("demo")
DEMO_TEAM = Membership("demo", "team")
OTHER = Membership("andere")

ORG_SCOPE = Scope(organisations=frozenset({"demo"}))
GROUP_SCOPE = Scope(groups=frozenset({"team"}))
EVERYTHING = Scope(everything=True)


def member(
    *memberships: Membership, roles: frozenset[Role] = frozenset({Role.ORG_ADMIN})
) -> StaffMember:
    return StaffMember("sub-1", "Olaf Org", roles, frozenset(memberships), NOW, NOW, NOW, id=7)


def test_an_organisation_scope_sees_members_of_the_organisation_and_its_groups() -> None:
    assert visible(member(DEMO), ORG_SCOPE)
    assert visible(member(DEMO_TEAM), ORG_SCOPE)
    assert not visible(member(OTHER), ORG_SCOPE)
    assert not visible(member(), ORG_SCOPE)


def test_a_group_scope_sees_only_members_of_the_group() -> None:
    assert visible(member(DEMO_TEAM), GROUP_SCOPE)
    assert not visible(member(DEMO), GROUP_SCOPE)
    assert not visible(member(Membership("demo", "elders")), GROUP_SCOPE)


def test_superusers_are_seen_by_the_superuser_only() -> None:
    superuser = member(DEMO, roles=frozenset({Role.SUPERUSER}))

    assert not visible(superuser, ORG_SCOPE)
    assert visible(superuser, EVERYTHING)
    assert visible(member(), EVERYTHING)


def test_within_hides_memberships_outside_the_scope() -> None:
    both = member(DEMO, DEMO_TEAM, OTHER)

    assert both.within(ORG_SCOPE).memberships == {DEMO, DEMO_TEAM}
    assert both.within(GROUP_SCOPE).memberships == {DEMO_TEAM}
    assert both.within(EVERYTHING) is both
    assert both.organisations == ["andere", "demo"]
    assert both.groups_of("demo") == ["team"]


def test_the_display_name_stays_out_of_repr() -> None:
    assert "Olaf" not in repr(member())


def test_dormant_after_the_configured_period() -> None:
    old = replace(member(), last_login_at=NOW - timedelta(days=91))

    assert old.is_dormant(NOW, timedelta(days=90))
    assert not member().is_dormant(NOW, timedelta(days=90))


def test_the_filter_narrows() -> None:
    m = member(DEMO_TEAM, OTHER)

    assert StaffFilter().matches(m)
    assert StaffFilter(organisation="andere").matches(m)
    assert not StaffFilter(organisation="elders").matches(m)
    assert StaffFilter(group="team").matches(m)
    assert not StaffFilter(group="elders").matches(m)
    assert StaffFilter(role=Role.ORG_ADMIN).matches(m)
    assert not StaffFilter(role=Role.AUDITOR).matches(m)
    assert StaffFilter(dormant_before=NOW + timedelta(seconds=1)).matches(m)
    assert not StaffFilter(dormant_before=NOW).matches(m)
    assert StaffFilter(id=7).matches(m)
    assert not StaffFilter(id=8).matches(m)


def test_a_first_login_is_recorded_in_each_membership() -> None:
    events = change_events(None, member(DEMO, DEMO_TEAM))

    assert [(e.action, e.organisation, e.group, e.detail) for e in events] == [
        (CREATED_EVENT, "demo", None, "role.add:org_admin,organisation.add:demo,group.add:team"),
        (CREATED_EVENT, "demo", "team", "role.add:org_admin,organisation.add:demo,group.add:team"),
    ]
    assert all(e.outcome is Outcome.SUCCESS and e.actor == "sub-1" for e in events)
    assert all(e.at == NOW for e in events)


def test_a_first_login_without_memberships_is_global() -> None:
    events = change_events(None, member(roles=frozenset({Role.SUPERUSER})))

    assert [(e.organisation, e.group, e.detail) for e in events] == [
        (None, None, "role.add:superuser")
    ]


def test_an_unchanged_login_writes_nothing() -> None:
    assert change_events(member(DEMO), member(DEMO)) == []


def test_a_record_from_before_the_directory_writes_nothing() -> None:
    legacy = replace(member(), roles=frozenset(), recorded=False)

    assert change_events(legacy, member(DEMO)) == []


def test_a_move_is_shown_to_both_organisations_without_the_other_ones_slug() -> None:
    events = change_events(member(DEMO), member(OTHER))

    assert [(e.action, e.organisation, e.detail) for e in events] == [
        (CHANGED_EVENT, "andere", "organisation.add:andere"),
        (CHANGED_EVENT, "demo", "organisation.remove:demo"),
    ]


def test_a_role_change_is_shown_in_every_membership() -> None:
    before = member(DEMO_TEAM, roles=frozenset({Role.GROUP_ADMIN}))
    after = member(DEMO_TEAM, roles=frozenset({Role.ORG_ADMIN}))

    events = change_events(before, after)

    assert [(e.group, e.detail) for e in events] == [
        ("team", "role.add:org_admin,role.remove:group_admin")
    ]


def test_losing_every_membership_is_still_shown_where_it_was() -> None:
    events = change_events(member(DEMO_TEAM), member())

    assert [(e.organisation, e.group, e.detail) for e in events] == [
        ("demo", "team", "group.remove:team")
    ]


def test_signed_in_until_the_session_lifetime_passes_or_a_logout() -> None:
    lifetime = timedelta(hours=8)
    seen = member()
    logged_out = replace(member(), last_logout_at=NOW)
    back = replace(
        logged_out,
        last_login_at=NOW + timedelta(minutes=5),
        last_seen_at=NOW + timedelta(minutes=5),
    )

    assert seen.is_signed_in(NOW + timedelta(hours=8), lifetime)
    assert not seen.is_signed_in(NOW + timedelta(hours=8, seconds=1), lifetime)
    assert not logged_out.is_signed_in(NOW, lifetime)
    assert back.is_signed_in(NOW + timedelta(minutes=6), lifetime)
