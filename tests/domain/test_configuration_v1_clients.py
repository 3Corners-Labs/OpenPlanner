"""The API client in a version-1 configuration document (ADR 0099)."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import ChangeAction, RuleContext, Usage
from app.domain.configuration_document import to_json
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_schema import Anchor, Collection, Format, Kind, Shape, strings, text
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_clients import API_CLIENT, INTEGRITY
from app.domain.configuration_values import Secret
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import Recorder, allow, entity, parse, plan, refusals
from tests.support.configuration_columns import uncovered_columns

CLIENT_ID = "opc_" + "a" * 20
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
DIGEST = "a" * 64
GROUP = Kind("group", key=("slug",), anchor=Anchor.GROUP, fields=(text("slug", required=True),))
ORGANISATION = Kind(
    "organisation",
    key=("slug",),
    permission=Permission.ORGANISATION_MANAGE,
    anchor=Anchor.ORGANISATION,
    fields=(text("slug", required=True), strings("unused")),
    collections=(Collection("groups", GROUP, Shape.ENTITIES),),
)
ROOT = Kind(
    "installation",
    collections=(
        Collection("organisations", ORGANISATION, Shape.ENTITIES),
        Collection("api_clients", API_CLIENT, Shape.ENTITIES),
    ),
)
FORMAT = Format("test/v1", ROOT, integrity=INTEGRITY)
NEW = {
    "client_id": CLIENT_ID,
    "name": "  Webex   bot ",
    "organisation": "acme",
    "scopes": ["availability:read"],
    "secret_expires_at": "2027-01-01T12:00:00+01:00",
}


def sealed(**fields: Any) -> dict[str, Any]:
    """Return a client as a document states it, with its sealed hash."""
    return {**NEW, "secret_hash": {"sealed": "c2VhbGVk"}, **fields}


def opened(data: dict[str, Any], current: Entity, secret: str = DIGEST) -> Plan:
    """Plan a document whose client secret the adapter has opened."""
    document = parse(data, FORMAT)
    (client,) = document.root.children["api_clients"]
    fields = {**client.fields, "secret_hash": Secret(secret)}
    root = replace(
        document.root,
        children={**document.root.children, "api_clients": (replace(client, fields=fields),)},
    )
    return plan_document(
        replace(document, root=root),
        current,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(now=NOW),
        settings_version=1,
    )


def world(*clients: Entity, groups: tuple[str, ...] = ("team",)) -> Entity:
    """Return the installation: organisation acme with some groups, and the clients."""
    acme = entity(
        "organisation",
        ("acme",),
        {"unused": []},
        {"groups": tuple(entity("group", (slug,)) for slug in groups)},
    )
    return entity("installation", (), {}, {"organisations": (acme,), "api_clients": clients})


def client(enabled: bool = False, **over: Any) -> Entity:
    """Return an existing client."""
    fields = {
        "name": "Bot",
        "organisation": "acme",
        "scopes": ["availability:read"],
        "networks": [],
        "enabled": enabled,
        "groups": [],
        "secret_hash": Secret(DIGEST),
        "secret_expires_at": "2027-01-01T11:00:00+00:00",
        **over,
    }
    return entity("api_client", (CLIENT_ID,), fields)


def planned(data: dict[str, Any], current: Entity | None = None, **options: Any) -> Plan:
    """Plan ``data`` in the test format."""
    options.setdefault("context", RuleContext(now=NOW))
    return plan(data, current if current is not None else world(), fmt=FORMAT, **options)


def test_a_client_is_created_normalised_with_its_secret_hash_and_the_right_permission() -> None:
    result = opened({"api_clients": [sealed()]}, world())

    (change,) = result.changes
    assert result.ok
    assert (change.action, change.address, change.permissions) == (
        ChangeAction.CREATE,
        (CLIENT_ID,),
        {Permission.API_CLIENTS_MANAGE},
    )
    assert change.organisation == "acme"
    assert change.values["name"] == "Webex bot"
    assert change.values["secret_expires_at"] == "2027-01-01T11:00:00+00:00"
    assert change.values["scopes"] == ["availability:read"]
    assert change.values["enabled"] is False
    assert change.values["groups"] == []


def test_a_client_is_planned_against_the_global_target() -> None:
    recorder = Recorder()

    opened_plan = opened({"api_clients": [sealed()]}, world())
    planned({"api_clients": [sealed()]}, may_change=recorder)

    assert opened_plan.ok
    assert (Permission.API_CLIENTS_MANAGE, Target.everything()) in recorder.asked


@pytest.mark.parametrize("missing", ["secret_hash", "secret_expires_at"])
def test_a_new_client_cannot_be_made_without_its_secret_hash_and_expiry(missing: str) -> None:
    data = {k: v for k, v in sealed().items() if k != missing}

    result = planned({"api_clients": [data]})

    assert refusals(result) == [
        (f"api_clients[{CLIENT_ID}]", ErrorCode.INVALID_CONFIGURATION, missing)
    ]


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"name": ""}, "name"),
        ({"scopes": []}, "scopes"),
        ({"scopes": ["root"]}, "scopes"),
        ({"networks": ["not-a-network"]}, "networks"),
        ({"networks": ["10.0.0.0/8"] * 1 + [f"10.0.{n}.0/24" for n in range(21)]}, "networks"),
    ],
)
def test_a_client_the_portal_would_refuse_is_refused(over: dict[str, Any], field: str) -> None:
    result = planned({"api_clients": [sealed(**over)]})

    assert refusals(result) == [
        (f"api_clients[{CLIENT_ID}]", ErrorCode.INVALID_CONFIGURATION, field)
    ]


def test_the_client_id_must_have_the_shape_the_portal_issues() -> None:
    result = planned({"api_clients": [sealed(client_id="bot-1")]})

    assert refusals(result) == [
        ("api_clients[bot-1]", ErrorCode.INVALID_CONFIGURATION, "client_id")
    ]


def test_the_expiry_needs_an_offset_and_the_hash_must_be_a_sha256() -> None:
    naive = planned({"api_clients": [sealed(secret_expires_at="2027-01-01T12:00:00")]})
    odd = planned({"api_clients": [sealed(secret_expires_at="next year")]})
    short = opened({"api_clients": [sealed()]}, world(), secret="not-a-hash")

    assert refusals(naive)[0][2] == "secret_expires_at"
    assert refusals(odd)[0][2] == "secret_expires_at"
    assert refusals(short)[0][2] == "secret_hash"


def test_networks_scopes_and_groups_are_written_normalised() -> None:
    current = world(groups=("team", "other"))
    data = sealed(networks=["10.0.0.1", "10.0.0.0/24"], groups=["team", "other"])

    (change,) = opened({"api_clients": [data]}, current).changes

    assert change.values["networks"] == ["10.0.0.1/32", "10.0.0.0/24"]
    assert change.values["groups"] == ["other", "team"]


def test_an_existing_client_changes_without_its_secret_and_keeps_it() -> None:
    current = world(client())

    result = planned(
        {"api_clients": [{"client_id": CLIENT_ID, "enabled": True, "name": "Renamed"}]}, current
    )

    (change,) = result.changes
    assert (change.action, change.fields) == (ChangeAction.UPDATE, ("name", "enabled"))
    assert change.values["secret_hash"] == Secret(DIGEST)


def test_the_same_secret_hash_is_no_change() -> None:
    current = world(client())

    result = opened({"api_clients": [sealed(enabled=False, name="Bot")]}, current)

    assert result.changes == ()


def test_the_organisation_and_groups_must_exist_in_what_would_result() -> None:
    lost = planned({"api_clients": [sealed(organisation="gone")]})
    stranger = planned({"api_clients": [sealed(groups=["nowhere"])]})
    fine = opened({"api_clients": [sealed(groups=["team"])]}, world())

    assert refusals(lost) == [
        (f"api_clients[{CLIENT_ID}]", ErrorCode.ORGANISATION_NOT_FOUND, "organisation")
    ]
    assert refusals(stranger) == [
        (f"api_clients[{CLIENT_ID}]", ErrorCode.INVALID_CONFIGURATION, "groups")
    ]
    assert fine.ok


def test_a_group_removed_by_the_same_document_cannot_stay_in_a_clients_list() -> None:
    current = world(client(groups=["team"]))
    data = {
        "organisations": [{"slug": "acme", "groups": [{"slug": "team", "action": "delete"}]}],
    }

    result = planned(data, current)

    assert refusals(result) == [
        (f"api_clients[{CLIENT_ID}]", ErrorCode.INVALID_CONFIGURATION, "groups")
    ]


def test_an_enabled_client_is_not_deleted_but_a_disabled_one_is() -> None:
    gone = {"api_clients": [{"client_id": CLIENT_ID, "action": "delete"}]}

    enabled = planned(gone, world(client(enabled=True)))
    disabled = planned(gone, world(client(enabled=False)))

    assert refusals(enabled) == [(f"api_clients[{CLIENT_ID}]", ErrorCode.API_CLIENT_ENABLED, None)]
    assert [c.action for c in disabled.changes] == [ChangeAction.DELETE]


def test_a_client_survives_reading_and_writing() -> None:
    data = {
        "api_clients": [
            sealed(name="Webex bot", networks=["10.0.0.0/24"], groups=["team"], enabled=True)
        ]
    }

    written = to_json(parse(data, FORMAT))

    assert written == {"format": "test/v1", **data}


def test_a_bad_hash_value_is_refused_when_the_document_is_read() -> None:
    with pytest.raises(DomainError):
        planned({"api_clients": [sealed(secret_hash="plain")]})


def test_every_column_of_the_client_tables_is_covered() -> None:
    assert uncovered_columns([API_CLIENT]) == {}
    assert API_CLIENT.tables == {"api_clients", "api_client_groups"}
    assert API_CLIENT.organisation_field == "organisation"


def test_a_secret_cannot_live_longer_than_the_portal_lets_it_and_a_new_one_not_be_over() -> None:
    far = planned({"api_clients": [sealed(secret_expires_at="2028-01-01T00:00:00+00:00")]})
    past = planned({"api_clients": [sealed(secret_expires_at="2026-01-01T00:00:00+00:00")]})
    edge = planned({"api_clients": [sealed(secret_expires_at="2027-11-08T00:00:00+00:00")]})

    assert refusals(far)[0][2] == "secret_expires_at"
    assert refusals(past)[0][2] == "secret_expires_at"
    assert not [r for r in refusals(edge) if r[2] == "secret_expires_at"]
