"""Use cases of the log destinations: rights, audit and what never reaches the audit log."""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.log_destination_service import LogDestinationService
from app.domain.log_destinations import (
    AUDIT_ACTION,
    DeliveryError,
    DestinationDraft,
    DestinationKind,
    Reason,
)
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.principals import principal

SUPER = principal("sub-super", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
AUDITOR = principal("sub-aud", "A", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())
ORG_ADMIN = principal("sub-org", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
TOKEN = "hec-token-0123456789"


def draft(**changes: object) -> DestinationDraft:
    values: dict[str, object] = {
        "name": "Splunk",
        "kind": DestinationKind.SPLUNK_HEC,
        "address": "https://splunk.example.org:8088/services/collector/event",
        "index": "openplanner",
    }
    values.update(changes)
    return DestinationDraft(**values)  # type: ignore[arg-type]


class Env:
    """The service with an in-memory database and a scriptable tester."""

    def __init__(self, *, production: bool = True, with_tester: bool = True) -> None:
        self.uow = InMemoryUnitOfWork()
        self.clock = FixedClock()
        self.changes = 0
        self.tested: list[int] = []
        self.failure: DeliveryError | None = None
        self.service = LogDestinationService(
            self.uow,
            self.clock,
            AdminService(self.uow, self.clock),
            production=production,
            tester=self._test if with_tester else None,
            on_change=self._changed,
        )

    def _changed(self) -> None:
        self.changes += 1

    async def _test(self, destination_id: int) -> None:
        self.tested.append(destination_id)
        if self.failure:
            raise self.failure

    def events(self) -> list[tuple[str, Outcome, str, str]]:
        return [
            (e.actor, e.outcome, e.reason, e.detail)
            for e in self.uow.state.audit
            if e.action == AUDIT_ACTION
        ]


@pytest.fixture
def env() -> Env:
    return Env()


async def test_the_superuser_creates_a_destination_switched_off_and_untested(env: Env) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)

    assert created.id is not None
    assert (created.enabled, created.secret_set, created.tested_at) == (False, True, None)
    assert env.uow.state.log_secrets[created.id] == TOKEN
    assert env.changes == 1
    assert env.events() == [("sub-super", Outcome.SUCCESS, "", f"created:splunk_hec:{created.id}")]


async def test_nothing_secret_reaches_the_audit_log_or_the_overview(env: Env) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)
    await env.service.update(
        SUPER, created.id or 0, draft(address="https://other.example.org"), TOKEN + "2"
    )

    audit = repr(env.uow.state.audit)
    assert TOKEN not in audit
    assert "splunk.example.org" not in audit
    assert "other.example.org" not in audit
    overview = await env.service.overview(AUDITOR)
    assert TOKEN not in repr(overview)


@pytest.mark.parametrize("who", [AUDITOR, ORG_ADMIN])
async def test_only_the_superuser_changes_anything(env: Env, who: object) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)
    call = env.service
    attempts = [
        call.create(who, draft(name="Other"), TOKEN),  # type: ignore[arg-type]
        call.update(who, created.id or 0, draft(name="Renamed"), None),  # type: ignore[arg-type]
        call.set_enabled(who, created.id or 0, True),  # type: ignore[arg-type]
        call.delete(who, created.id or 0),  # type: ignore[arg-type]
        call.send_test(who, created.id or 0),  # type: ignore[arg-type]
    ]
    for attempt in attempts:
        with pytest.raises(DomainError) as caught:
            await attempt
        assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert env.tested == []
    assert (await env.service.overview(SUPER)).destinations[0].name == "Splunk"


async def test_the_auditor_reads_and_the_organisation_admin_does_not(env: Env) -> None:
    await env.service.create(SUPER, draft(), TOKEN)

    assert len((await env.service.overview(AUDITOR)).destinations) == 1
    with pytest.raises(DomainError) as caught:
        await env.service.overview(ORG_ADMIN)
    assert caught.value.code is ErrorCode.NOT_PERMITTED


async def test_a_kind_that_needs_a_credential_refuses_to_start_without_one(env: Env) -> None:
    with pytest.raises(DomainError) as caught:
        await env.service.create(SUPER, draft(), None)

    assert (caught.value.code, caught.value.field) == (ErrorCode.LOG_DESTINATION_INVALID, "secret")
    assert env.uow.state.log_destinations == {}


async def test_syslog_and_http_need_no_credential_but_syslog_takes_none(env: Env) -> None:
    syslog = await env.service.create(
        SUPER,
        draft(
            name="Syslog", kind=DestinationKind.SYSLOG, address="syslog+tls://s.example.org:6514"
        ),
        None,
    )
    plain = await env.service.create(
        SUPER,
        draft(name="HTTP", kind=DestinationKind.HTTP, address="https://c.example.org/x"),
        None,
    )

    assert not syslog.secret_set
    assert not plain.secret_set
    with pytest.raises(DomainError):
        await env.service.create(
            SUPER,
            draft(
                name="Syslog 2",
                kind=DestinationKind.SYSLOG,
                address="syslog+tls://s.example.org:6514",
            ),
            "token",
        )


async def test_a_name_is_unique(env: Env) -> None:
    await env.service.create(SUPER, draft(), TOKEN)

    with pytest.raises(DomainError) as caught:
        await env.service.create(SUPER, draft(), TOKEN)
    assert caught.value.code is ErrorCode.LOG_DESTINATION_NAME_TAKEN


async def test_a_production_installation_refuses_unencrypted_addresses() -> None:
    env = Env(production=True)
    with pytest.raises(DomainError) as caught:
        await env.service.create(
            SUPER, draft(kind=DestinationKind.HTTP, address="http://c.example.org/x"), None
        )
    assert caught.value.code is ErrorCode.LOG_DESTINATION_INSECURE

    development = Env(production=False)
    created = await development.service.create(
        SUPER, draft(kind=DestinationKind.HTTP, address="http://c.example.org/x"), None
    )
    assert created.id is not None


async def test_an_update_lists_what_changed_and_a_blank_credential_keeps_the_stored_one(
    env: Env,
) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)
    destination_id = created.id or 0

    changed = await env.service.update(SUPER, destination_id, draft(min_level="error"), None)

    assert changed == ["min_level"]
    assert env.uow.state.log_secrets[destination_id] == TOKEN
    assert env.events()[-1] == (
        "sub-super",
        Outcome.SUCCESS,
        "",
        f"changed:splunk_hec:{destination_id}:min_level",
    )
    assert await env.service.update(SUPER, destination_id, draft(min_level="error"), None) == []
    assert len(env.events()) == 2  # an update that changes nothing writes nothing


async def test_a_new_address_or_credential_makes_the_destination_untested_again(env: Env) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)
    destination_id = created.id or 0
    assert (await env.service.send_test(SUPER, destination_id)).ok
    assert env.uow.state.log_destinations[destination_id].tested_at == env.clock.now()

    await env.service.update(SUPER, destination_id, draft(min_level="error"), None)
    assert env.uow.state.log_destinations[destination_id].tested_at is not None

    await env.service.update(SUPER, destination_id, draft(min_level="error"), "another-token")
    assert env.uow.state.log_destinations[destination_id].tested_at is None
    assert env.uow.state.log_secrets[destination_id] == "another-token"


async def test_switching_on_and_off_is_audited_once_per_change(env: Env) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)
    destination_id = created.id or 0

    assert await env.service.set_enabled(SUPER, destination_id, True) is True
    assert await env.service.set_enabled(SUPER, destination_id, True) is False
    assert await env.service.set_enabled(SUPER, destination_id, False) is True

    details = [detail for *_, detail in env.events()]
    assert details == [
        f"created:splunk_hec:{destination_id}",
        f"enabled:splunk_hec:{destination_id}",
        f"disabled:splunk_hec:{destination_id}",
    ]


async def test_delete_removes_the_destination_its_credential_and_status(env: Env) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)
    destination_id = created.id or 0

    await env.service.delete(SUPER, destination_id)

    assert env.uow.state.log_destinations == {}
    assert env.uow.state.log_secrets == {}
    with pytest.raises(DomainError) as caught:
        await env.service.destination(SUPER, destination_id)
    assert caught.value.code is ErrorCode.LOG_DESTINATION_NOT_FOUND
    assert env.events()[-1][3] == f"deleted:splunk_hec:{destination_id}"


async def test_a_test_line_that_arrives_marks_the_destination_tested(env: Env) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)

    result = await env.service.send_test(SUPER, created.id or 0)

    assert result.ok
    assert env.tested == [created.id]
    assert env.uow.state.log_destinations[created.id or 0].tested_at == env.clock.now()
    assert env.events()[-1][:3] == ("sub-super", Outcome.SUCCESS, "")


async def test_a_test_line_that_fails_is_audited_with_its_reason_and_code_only(env: Env) -> None:
    created = await env.service.create(SUPER, draft(), TOKEN)
    env.failure = DeliveryError(Reason.REFUSED, 403)

    result = await env.service.send_test(SUPER, created.id or 0)

    assert (result.ok, result.reason, result.code) == (False, "refused", 403)
    assert env.uow.state.log_destinations[created.id or 0].tested_at is None
    assert env.events()[-1] == (
        "sub-super",
        Outcome.FAILURE,
        "refused",
        f"tested:splunk_hec:{created.id}",
    )


async def test_without_a_sender_a_test_is_not_ready() -> None:
    env = Env(with_tester=False)
    created = await env.service.create(SUPER, draft(), TOKEN)

    with pytest.raises(DomainError) as caught:
        await env.service.send_test(SUPER, created.id or 0)
    assert caught.value.code is ErrorCode.INTEGRATION_NOT_READY
