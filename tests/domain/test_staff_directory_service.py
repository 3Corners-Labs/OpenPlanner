"""Managing staff in the bundled identity provider through the portal (ADR 0095)."""

import pytest

from app.domain.admin import AdminService
from app.domain.authorization import Grant
from app.domain.errors import DomainError, ErrorCode
from app.domain.staff_directory import DirectoryUser, NewStaffMember, StaffAction
from app.domain.staff_directory_service import DirectorySettings, StaffDirectoryService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.fakes.staff_directory import FakeStaffDirectory
from tests.support.admin import (
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SUPER,
    audit_rows,
    two_organisations,
)
from tests.support.domain import NOW

P = "openplanner"
SETTINGS = DirectorySettings(enabled=True, prefix=P, invite_expiry_days=7)
NEW = NewStaffMember("nina@example.test", "Nina", "Nieuw")


def person(user_id: str, *values: str, enabled: bool = True) -> DirectoryUser:
    return DirectoryUser(
        user_id, f"{user_id}@x.test", f"{user_id}@x.test", "A", "B", enabled, frozenset(values)
    )


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def directory() -> FakeStaffDirectory:
    return FakeStaffDirectory(
        [
            person("kc-mine", f"{P}:agent:demo/demo"),
            person("kc-both", f"{P}:org_admin:demo", f"{P}:org_admin:andere"),
            person("kc-other", f"{P}:org_admin:andere"),
        ]
    )


def service(
    uow: InMemoryUnitOfWork,
    directory: FakeStaffDirectory | None,
    settings: DirectorySettings = SETTINGS,
) -> StaffDirectoryService:
    clock = FixedClock(NOW)
    return StaffDirectoryService(uow, clock, AdminService(uow, clock), directory, lambda: settings)


async def code_of(call: object) -> ErrorCode:
    with pytest.raises(DomainError) as caught:
        await call  # type: ignore[misc]
    return caught.value.code


async def test_an_organisation_admin_invites_into_the_own_organisation(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    await service(uow, directory).invite(ORG_ADMIN, NEW, [Grant("agent", "demo", "demo")])

    created = next(u for u in directory.users.values() if u.email == NEW.email)
    assert created.entitlements == {f"{P}:agent:demo/demo"}
    assert directory.mails == [("kc-1", {StaffAction.UPDATE_PASSWORD}, 7 * 86400)]
    [(actor, action, _, detail, _)] = audit_rows(uow)
    assert (actor, action, detail) == ("o-1", "staff.manage", "directory.created:agent:demo/demo")


async def test_the_audit_event_holds_no_personal_data(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    await service(uow, directory).invite(ORG_ADMIN, NEW, [Grant("agent", "demo", "demo")])

    assert "nina" not in repr(uow.state.audit).lower()
    assert "Nieuw" not in repr(uow.state.audit)


async def test_an_invitation_names_a_place_in_scope(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    code = await code_of(
        service(uow, directory).invite(ORG_ADMIN, NEW, [Grant("org_admin", "andere")])
    )

    assert code is ErrorCode.STAFF_GRANT_REFUSED
    assert not any(u.email == NEW.email for u in directory.users.values())


async def test_an_organisation_admin_cannot_invite_a_superuser(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    code = await code_of(service(uow, directory).invite(ORG_ADMIN, NEW, [Grant("superuser")]))

    assert code is ErrorCode.STAFF_GRANT_REFUSED


async def test_the_superuser_invites_a_superuser(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    await service(uow, directory).invite(SUPER, NEW, [Grant("superuser")])

    assert any(f"{P}:superuser" in u.entitlements for u in directory.users.values())


@pytest.mark.parametrize("who", [GROUP_ADMIN, AUDITOR])
async def test_roles_without_staff_manage_cannot_invite(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory, who: object
) -> None:
    code = await code_of(service(uow, directory).invite(who, NEW, [Grant("agent", "demo", "demo")]))  # type: ignore[arg-type]

    assert code is ErrorCode.NOT_PERMITTED


async def test_an_invitation_without_a_role_is_refused(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    assert await code_of(service(uow, directory).invite(ORG_ADMIN, NEW, [])) is (
        ErrorCode.INVALID_CONFIGURATION
    )


async def test_an_address_in_use_is_reported(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    await service(uow, directory).invite(ORG_ADMIN, NEW, [Grant("agent", "demo", "demo")])

    code = await code_of(
        service(uow, directory).invite(ORG_ADMIN, NEW, [Grant("agent", "demo", "demo")])
    )

    assert code is ErrorCode.STAFF_ADDRESS_IN_USE


async def test_without_a_directory_nothing_is_possible(uow: InMemoryUnitOfWork) -> None:
    unavailable = service(uow, None)
    switched_off = service(uow, FakeStaffDirectory(), DirectorySettings(False, P, 7))

    for candidate in (unavailable, switched_off):
        assert not candidate.available
        assert await code_of(candidate.invite(SUPER, NEW, [Grant("superuser")])) is (
            ErrorCode.STAFF_DIRECTORY_UNAVAILABLE
        )


async def test_changing_roles_replaces_only_values_in_scope(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    await service(uow, directory).set_grants(ORG_ADMIN, "kc-both", [Grant("auditor", "demo")])

    assert directory.users["kc-both"].entitlements == {
        f"{P}:auditor:demo",
        f"{P}:org_admin:andere",
    }


async def test_an_organisation_admin_cannot_touch_someone_outside_the_scope(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    for call in (
        service(uow, directory).set_grants(ORG_ADMIN, "kc-other", [Grant("auditor", "demo")]),
        service(uow, directory).set_enabled(ORG_ADMIN, "kc-other", False),
        service(uow, directory).send_reset(ORG_ADMIN, "kc-other"),
    ):
        assert await code_of(call) is ErrorCode.STAFF_NOT_FOUND
    assert directory.users["kc-other"].enabled


async def test_a_person_who_also_works_elsewhere_is_not_disabled_by_one_organisation(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    assert (
        await code_of(service(uow, directory).set_enabled(ORG_ADMIN, "kc-both", False))
        is ErrorCode.NOT_PERMITTED
    )
    await service(uow, directory).set_enabled(SUPER, "kc-both", False)

    assert not directory.users["kc-both"].enabled


async def test_disabling_and_enabling_a_person_in_scope(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    await service(uow, directory).set_enabled(ORG_ADMIN, "kc-mine", False)
    assert not directory.users["kc-mine"].enabled

    await service(uow, directory).set_enabled(ORG_ADMIN, "kc-mine", True)
    assert directory.users["kc-mine"].enabled
    assert [row[3] for row in audit_rows(uow)] == ["directory.disabled", "directory.enabled"]


async def test_a_password_reset_mails_the_link(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    await service(uow, directory).send_reset(ORG_ADMIN, "kc-mine")

    assert directory.mails == [("kc-mine", {StaffAction.UPDATE_PASSWORD}, 7 * 86400)]
    assert audit_rows(uow)[0][1] == "staff.reset_password"


async def test_pending_lists_invited_people_in_scope_who_never_logged_in(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    mine = await service(uow, directory).pending(ORG_ADMIN)
    everyone = await service(uow, directory).pending(SUPER)
    outsider = await service(uow, directory).pending(OUTSIDER)

    assert sorted(p.user.id for p in mine) == ["kc-both", "kc-mine"]
    assert sorted(p.user.id for p in everyone) == ["kc-both", "kc-mine", "kc-other"]
    assert sorted(p.user.id for p in outsider) == ["kc-both", "kc-other"]
    assert [g.key for g in next(p for p in mine if p.user.id == "kc-both").grants] == [
        "org_admin:demo"
    ]


async def test_a_provider_that_does_not_answer_is_one_error(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    directory.fail = "unreachable"

    assert await code_of(service(uow, directory).set_enabled(SUPER, "kc-mine", False)) is (
        ErrorCode.STAFF_DIRECTORY_UNAVAILABLE
    )


async def test_a_mail_the_provider_cannot_send_is_reported(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    directory.fail = "mail"

    assert await code_of(service(uow, directory).send_reset(SUPER, "kc-mine")) is (
        ErrorCode.STAFF_MAIL_FAILED
    )


async def test_a_superuser_who_also_works_here_is_the_superusers_alone(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    directory.users["kc-root"] = person("kc-root", f"{P}:superuser", f"{P}:org_admin:demo")

    assert "kc-root" not in [p.user.id for p in await service(uow, directory).pending(ORG_ADMIN)]
    for call in (
        service(uow, directory).set_grants(ORG_ADMIN, "kc-root", []),
        service(uow, directory).send_reset(ORG_ADMIN, "kc-root"),
        service(uow, directory).set_enabled(ORG_ADMIN, "kc-root", False),
    ):
        assert await code_of(call) is ErrorCode.STAFF_NOT_FOUND
    assert "kc-root" in [p.user.id for p in await service(uow, directory).pending(SUPER)]


async def test_nobody_changes_themselves(
    uow: InMemoryUnitOfWork, directory: FakeStaffDirectory
) -> None:
    directory.users["o-1"] = person("o-1", f"{P}:org_admin:demo")

    assert await code_of(service(uow, directory).set_enabled(ORG_ADMIN, "o-1", False)) is (
        ErrorCode.NOT_PERMITTED
    )
    assert await code_of(service(uow, directory).set_grants(ORG_ADMIN, "o-1", [])) is (
        ErrorCode.NOT_PERMITTED
    )
