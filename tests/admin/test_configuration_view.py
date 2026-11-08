"""The plan as the plan page shows it: rows, refusals and the warnings to read first."""

from app.admin.configuration_view import plan_view
from app.domain.configuration_changes import Change, ChangeAction, Refusal
from app.domain.configuration_plan import Plan
from app.domain.configuration_v1_names import PLATFORM_SECRET_KIND
from app.domain.errors import ErrorCode


def _(key: str, **_values: object) -> str:
    return key


def change(kind: str, action: ChangeAction, key: tuple[str, ...], *fields: str) -> Change:
    return Change(kind, key, key, "/".join(key), action, fields)


def test_changes_become_rows_with_field_names_and_deletes_have_none() -> None:
    plan = Plan(
        (
            change("group", ChangeAction.UPDATE, ("acme", "team"), "name", "timezone"),
            change("group", ChangeAction.DELETE, ("acme", "old")),
        ),
        (),
        "abc",
    )

    view = plan_view(plan, _)

    assert [(r.action, r.fields) for r in view.changes] == [
        ("update", "name, timezone"),
        ("delete", ""),
    ]
    assert view.deletes == 1
    assert view.can_apply
    assert view.plan_hash == "abc"
    assert view.warnings == ("admin.configuration.warning.deletes",)


def test_a_refusal_stops_the_apply_and_is_explained() -> None:
    plan = Plan(
        (change("group", ChangeAction.UPDATE, ("a", "b"), "name"),),
        (Refusal("organisations[a].groups[b]", ErrorCode.NOT_PERMITTED, "name"),),
        "abc",
    )

    view = plan_view(plan, _)

    assert not view.can_apply
    assert [(r.path, r.field) for r in view.refusals] == [("organisations[a].groups[b]", "name")]


def test_claim_values_and_the_webex_tokens_warn() -> None:
    plan = Plan(
        (
            change("organisation", ChangeAction.UPDATE, ("acme",), "idp_key"),
            change(PLATFORM_SECRET_KIND, ChangeAction.UPDATE, ("webex_service_app",), "secret"),
        ),
        (),
        "abc",
    )

    assert plan_view(plan, _).warnings == (
        "admin.configuration.warning.claims",
        "admin.configuration.warning.webex",
    )


def test_a_plan_without_changes_cannot_be_applied() -> None:
    assert not plan_view(Plan((), (), "abc"), _).can_apply
