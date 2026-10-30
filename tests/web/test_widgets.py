"""The widget register (ADR 0061 §2): every widget declared once, with a permission and titles."""

from fastapi.routing import APIRoute

from app.admin.authz import RequirePermission, can_function
from app.config import Settings
from app.domain.authorization import Permission, Principal, Role
from app.i18n import CATALOGUE_DIR, load_catalogues
from app.main import create_app
from app.web.widgets import (
    DEFAULT_LAYOUTS,
    WIDGETS,
    View,
    WidgetScope,
    default_layout,
    fragment_path,
    visible,
    widget,
)
from tests.admin.conftest import AGENT, AUDITOR, GROUP_ADMIN, SUPER
from tests.support.routes import all_routes

REQUIRED_LOCALES = ("nl", "en")


def test_the_seven_widgets_of_the_adr_are_declared_once() -> None:
    keys = [w.key for w in WIDGETS]

    assert keys == [
        "alerts",
        "queue",
        "bookings-today",
        "changes",
        "tasks",
        "status",
        "rights",
    ]
    assert len(keys) == len(set(keys))


def test_every_widget_has_a_permission_from_the_matrix() -> None:
    for w in WIDGETS:
        assert isinstance(w.permission, Permission), w.key


def test_every_widget_has_a_title_in_dutch_and_english() -> None:
    catalogues = load_catalogues(CATALOGUE_DIR)

    for w in WIDGETS:
        for locale in REQUIRED_LOCALES:
            assert w.title_key in catalogues[locale], (w.key, locale)
            assert catalogues[locale][w.title_key].strip(), (w.key, locale)


def test_every_widget_declares_scope_views_rows_live_and_topic() -> None:
    for w in WIDGETS:
        assert isinstance(w.scope, WidgetScope), w.key
        assert w.views, w.key
        assert all(isinstance(v, View) for v in w.views), w.key
        assert w.default_rows > 0, w.key
        assert isinstance(w.live, bool), w.key
        assert w.topic == "overview", w.key


def test_the_adr_table_is_followed() -> None:
    by_key = {w.key: w for w in WIDGETS}

    assert by_key["alerts"].permission is Permission.DASHBOARD_VIEW
    assert by_key["queue"].permission is Permission.QUEUE_WORK
    assert by_key["bookings-today"].permission is Permission.BOOKINGS_VIEW
    assert by_key["changes"].permission is Permission.AUDIT_VIEW
    assert by_key["tasks"].permission is Permission.DASHBOARD_VIEW
    assert by_key["status"].permission is Permission.SETTINGS_PLATFORM_VIEW
    assert by_key["rights"].permission is Permission.RIGHTS_VIEW
    assert not by_key["changes"].live
    assert not by_key["rights"].live
    assert all(by_key[k].live for k in ("alerts", "queue", "bookings-today", "tasks", "status"))


def test_only_the_widgets_of_this_step_are_available() -> None:
    assert {w.key for w in WIDGETS if w.available} == {"bookings-today", "changes", "rights"}


def test_every_role_has_a_default_layout_of_known_widgets() -> None:
    keys = {w.key for w in WIDGETS}

    assert set(DEFAULT_LAYOUTS) == set(Role)
    for role, layout in DEFAULT_LAYOUTS.items():
        assert layout, role
        assert set(layout) <= keys, role
        assert len(layout) == len(set(layout)), role


def test_the_default_layout_merges_the_roles_in_order() -> None:
    agent_then_auditor = Principal(
        "x", "X", frozenset({Role.AGENT, Role.AUDITOR}), frozenset({"demo"}), frozenset({"demo"})
    )

    assert [w.key for w in default_layout(SUPER)] == list(DEFAULT_LAYOUTS[Role.SUPERUSER])
    merged = [w.key for w in default_layout(agent_then_auditor)]
    # Roles merge in the order of ``Role`` (the broader role first): auditor, then agent.
    assert merged[: len(DEFAULT_LAYOUTS[Role.AUDITOR])] == list(DEFAULT_LAYOUTS[Role.AUDITOR])
    assert set(merged) == set(DEFAULT_LAYOUTS[Role.AGENT]) | set(DEFAULT_LAYOUTS[Role.AUDITOR])


def test_a_principal_without_a_built_in_role_gets_every_widget() -> None:
    custom_only = Principal("c", "C", frozenset(), frozenset({"demo"}), frozenset())

    assert [w.key for w in default_layout(custom_only)] == [w.key for w in WIDGETS]


def test_role_defaults_follow_what_each_role_cares_about() -> None:
    assert DEFAULT_LAYOUTS[Role.AGENT][0] == "queue"
    assert DEFAULT_LAYOUTS[Role.AUDITOR][0] == "changes"
    assert "rights" in DEFAULT_LAYOUTS[Role.GROUP_ADMIN]
    assert "status" not in DEFAULT_LAYOUTS[Role.AGENT]


def test_widget_lookup_and_fragment_path() -> None:
    assert widget("rights") is not None
    assert widget("nope") is None
    assert fragment_path(WIDGETS[-1]) == "/admin/overview/w/rights"


def _permissions(route: APIRoute) -> list[Permission]:
    found: list[Permission] = []
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if isinstance(dependency.call, RequirePermission):
            found.append(dependency.call.permission)
        stack.extend(dependency.dependencies)
    return found


def test_every_available_widget_has_a_fragment_route_with_its_own_permission(
    settings: Settings,
) -> None:
    routes = {r.path: r for r in all_routes(create_app(settings).routes)}

    for w in WIDGETS:
        path = fragment_path(w)
        if not w.available:
            assert path not in routes, f"{w.key} is not available yet and must have no route"
            continue
        assert path in routes, f"{w.key} needs a fragment route"
        assert _permissions(routes[path]) == [w.permission], w.key


def test_visible_widgets_follow_the_matrix() -> None:
    def shown(principal: Principal) -> list[str]:
        return [w.key for w in visible(principal, can_function(principal))]

    assert shown(SUPER) == ["bookings-today", "changes", "rights"]
    assert shown(AUDITOR) == ["changes", "rights"]
    assert shown(AGENT) == ["rights"]
    assert shown(GROUP_ADMIN) == ["bookings-today", "changes", "rights"]
