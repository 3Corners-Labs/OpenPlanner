"""*Add widget* and *Edit overview* (ADR 0096 step 3): keyboard-only forms, own layout only."""

import re

from tests.support.admin_site import AGENT, GROUP_ADMIN, SAME_ORIGIN, SUPER, Site

ADD = "/admin/overview/add"
EDIT = "/admin/overview/edit"


def _widgets(page: str) -> list[str]:
    """Return the keys of the widgets on the overview, in order."""
    return re.findall(r'data-widget="([^"]+)"', page)


def _offered(page: str) -> list[str]:
    return re.findall(r'name="widget" value="([^"]+)"', page)


async def test_the_overview_has_the_two_layout_controls(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get("/admin/?lang=en")).text

    assert f'href="{ADD}"' in page
    assert f'href="{EDIT}"' in page
    assert "Add widget" in page


async def test_an_admin_removes_moves_adds_and_restores(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    before = _widgets((await site.client.get("/admin/")).text)
    first, second = before[0], before[1]

    removed = await site.client.post(
        EDIT, data={"widget": first, "step": "remove"}, headers=SAME_ORIGIN
    )
    moved = await site.client.post(
        EDIT, data={"widget": before[-1], "step": "up"}, headers=SAME_ORIGIN
    )
    offer = _offered((await site.client.get(ADD)).text)
    added = await site.client.post(ADD, data={"widget": first}, headers=SAME_ORIGIN)
    after = _widgets((await site.client.get("/admin/")).text)

    assert removed.status_code == moved.status_code == 303
    assert removed.headers["location"].startswith(f"{EDIT}?done=widget_removed")
    assert first in offer
    assert added.headers["location"] == f"/admin/?done=widget_added#widget-{first}"
    assert after[0] == second
    assert after[-1] == first

    reset = await site.client.post("/admin/overview/reset", headers=SAME_ORIGIN)
    assert reset.headers["location"] == "/admin/?done=layout_reset"
    assert _widgets((await site.client.get("/admin/")).text) == before


async def test_a_widget_the_matrix_refuses_is_neither_offered_nor_added(site: Site) -> None:
    """The agent holds no settings.platform.view: *System status* is never theirs."""
    await site.login_as(AGENT)

    offer = _offered((await site.client.get(ADD)).text)
    refused = await site.client.post(ADD, data={"widget": "status"}, headers=SAME_ORIGIN)
    unknown = await site.client.post(
        EDIT, data={"widget": "nope", "step": "up"}, headers=SAME_ORIGIN
    )

    assert "status" not in offer
    assert refused.status_code == 422
    assert unknown.status_code == 422
    assert "status" not in _widgets((await site.client.get("/admin/")).text)


async def test_each_admin_has_their_own_layout(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.post(EDIT, data={"widget": "alerts", "step": "remove"}, headers=SAME_ORIGIN)
    await site.login_as(GROUP_ADMIN)

    assert "alerts" in _widgets((await site.client.get("/admin/")).text)


async def test_the_edit_page_has_a_button_per_step_and_no_script(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get(f"{EDIT}?lang=en")).text

    assert "Move " in page
    assert "Remove " in page
    assert "<script" not in page.split("<main", 1)[1]
    assert "Restore default layout" not in page  # only with an own layout


async def test_a_move_keeps_focus_on_the_widget_and_a_no_op_stores_nothing(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    shown = _widgets((await site.client.get("/admin/")).text)

    nothing = await site.client.post(
        EDIT, data={"widget": shown[0], "step": "up"}, headers=SAME_ORIGIN
    )
    page = (await site.client.get(f"{EDIT}?lang=en")).text
    moved = await site.client.post(
        EDIT, data={"widget": shown[1], "step": "up"}, headers=SAME_ORIGIN
    )

    assert "default layout of your role" in page  # the first widget up changed nothing
    assert nothing.status_code == 303
    assert moved.headers["location"].endswith(f"#layout-{shown[1]}")
    assert f'id="layout-{shown[1]}" tabindex="-1"' in (await site.client.get(EDIT)).text
