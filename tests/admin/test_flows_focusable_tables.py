"""The scrollable tables of the flow screens take focus (axe: scrollable-region-focusable)."""

from tests.support.admin_site import ORG_ADMIN, Site
from tests.support.flow_seed import add_flow, add_subjects
from tests.support.skill_seed import add_pool

BASE = "/admin/groups/demo/flows"


async def test_the_scrollable_tables_can_take_focus(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    await add_flow(site, "advies", await add_pool(site, "Mine"))

    flows = (await site.client.get(BASE)).text
    editor = (await site.client.get(f"{BASE}/advies")).text

    assert (
        'class="table-scroll" role="region" aria-labelledby="flows-heading" tabindex="0"' in flows
    )
    assert 'id="flows-heading"' in flows
    assert (
        'class="table-scroll" role="region" aria-labelledby="subjects-legend" tabindex="0"'
        in editor
    )
    assert 'id="subjects-legend"' in editor
