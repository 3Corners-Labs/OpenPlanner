"""The chart views of the overview widgets: server-rendered SVG (ADR 0061 §5, OV-4)."""

import re
from dataclasses import replace

import pytest

from app.admin.overview_charts import (
    CHART_SERIES,
    explicit_views,
    page_href,
)
from app.web.widgets import WIDGETS, View, widget
from tests.admin.conftest import AGENT, GROUP_ADMIN, ORG_ADMIN, SUPER, Site
from tests.support.queue import own_queue, site_uow

SVG = re.compile(r"<svg [^>]*>")
LABEL = re.compile(r'aria-label="([^"]*)"')


def _svg_label(html: str) -> str:
    [tag] = SVG.findall(html)
    found = LABEL.search(tag)
    assert found, tag
    return found.group(1)


async def _more_groups(site: Site, count: int) -> None:
    """Add ``count`` groups to the demo organisation, so a chart has more than four items."""
    async with site_uow(site).locked() as tx:
        demo = await tx.get_group_summary("demo")
        assert demo is not None
        for number in range(count):
            await tx.add_group(replace(demo.group, slug=f"extra-{number}", name=f"Extra {number}"))


def _no_inline_style(html: str) -> None:
    assert "style=" not in html
    assert "<script" not in html


# --- the bookings widget: list, bar, pie ----------------------------------------------------


async def test_the_default_view_of_the_bookings_widget_is_still_the_list(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (await site.client.get("/admin/overview/w/bookings-today")).text

    assert 'class="widget-list"' in text
    assert "<svg" not in text


async def test_the_bookings_widget_draws_a_bar_chart_with_every_value_as_text(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (await site.client.get("/admin/overview/w/bookings-today?view=bar")).text

    assert 'role="img"' in text
    assert _svg_label(text).startswith("Staafdiagram, Afspraken vandaag per groep: ")
    assert "Demo: videogesprek: 0 afspraken vandaag" in _svg_label(text)
    assert 'class="chart__value"' in text  # the value is printed on the bar
    assert 'class="widget-list"' not in text
    _no_inline_style(text)


async def test_a_bar_chart_lies_down_above_four_groups(site: Site) -> None:
    await _more_groups(site, 4)
    await site.login_as(SUPER)

    horizontal = (await site.client.get("/admin/overview/w/bookings-today?view=bar")).text
    pie = (await site.client.get("/admin/overview/w/bookings-today?view=pie")).text

    assert 'viewBox="0 0 320 ' in horizontal
    assert 'text-anchor="end"' in horizontal  # labels to the left of the bars
    assert horizontal.count('class="chart__bar chart-series-') == 6
    assert horizontal.count('<th scope="row">') == 6  # the same six in the table
    assert pie.count('class="chart-legend__item"') == 6


async def test_the_bookings_widget_draws_a_pie_with_a_legend_and_shares(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (await site.client.get("/admin/overview/w/bookings-today?view=pie")).text

    assert _svg_label(text).startswith("Taartdiagram, Afspraken vandaag per groep: ")
    assert 'class="chart-legend"' in text
    assert "Demo: videogesprek: 0 (0%)" in text
    assert "<path" not in text  # every count is zero: no slice, but the legend still lists them
    _no_inline_style(text)


async def test_every_chart_has_its_data_as_a_table_behind_details(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    for view in ("bar", "pie"):
        text = (await site.client.get(f"/admin/overview/w/bookings-today?view={view}")).text

        assert "<details" in text
        assert "<summary>Gegevens als tabel</summary>" in text
        assert '<th scope="col">Groep</th>' in text
        assert '<th scope="col" class="widget-table__number">Afspraken vandaag</th>' in text
        assert '<th scope="row">Demo: videogesprek</th>' in text


async def test_a_view_the_widget_does_not_support_falls_back_to_the_list(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    for asked in ("line", "table", "nope", "%3Cscript%3E", ""):
        text = (await site.client.get(f"/admin/overview/w/bookings-today?view={asked}")).text

        assert 'class="widget-list"' in text, asked
        assert "<svg" not in text, asked
        assert "<script" not in text, asked
        assert "nope" not in text, asked  # the value is never echoed


async def test_an_overlong_view_parameter_is_refused_by_validation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    assert (
        await site.client.get("/admin/overview/w/bookings-today?view=" + "x" * 40)
    ).status_code == 422


# --- the queue widget: table, bar, line -----------------------------------------------------


async def test_the_queue_widget_stays_a_table_by_default_and_for_a_pie(site: Site) -> None:
    await own_queue(site)
    await site.login_as(AGENT)
    for query in ("", "?view=pie", "?view=list"):
        text = (await site.client.get(f"/admin/overview/w/queue{query}")).text

        assert 'class="widget-table"' in text, query
        assert "<svg" not in text, query


async def test_the_queue_widget_draws_waiting_per_group_as_bar_and_line(site: Site) -> None:
    await own_queue(site)
    await site.login_as(GROUP_ADMIN)
    bar = (await site.client.get("/admin/overview/w/queue?view=bar")).text
    line = (await site.client.get("/admin/overview/w/queue?view=line")).text

    assert (
        _svg_label(bar) == "Staafdiagram, Wachtrij in mijn groepen: Demo: videogesprek: 0 wachtend"
    )
    assert _svg_label(line).startswith("Lijndiagram, Wachtrij in mijn groepen: ")
    assert 'class="chart__line"' in line
    assert 'class="chart__marker"' in line
    assert 'class="chart__bar chart-series-0"' in bar
    for text in (bar, line):
        assert '<th scope="col" class="widget-table__number">Wachtend</th>' in text
        assert "Ada" not in text
        _no_inline_style(text)


async def test_a_chart_has_the_empty_state_when_there_is_nothing_to_draw(site: Site) -> None:
    await site.login_as(AGENT)  # no own queue in reach
    text = (await site.client.get("/admin/overview/w/queue?view=bar")).text

    assert "<svg" not in text
    assert "Geen eigen wachtrij in uw bereik." in text


async def test_the_english_chart_texts(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (await site.client.get("/admin/overview/w/bookings-today?view=bar&lang=en")).text

    assert _svg_label(text).startswith("Bar chart, Bookings today per group: ")
    assert "<summary>Data as a table</summary>" in text


# --- the switcher on the page ---------------------------------------------------------------


async def test_the_page_draws_the_views_the_address_asks_for(site: Site) -> None:
    await own_queue(site)
    await site.login_as(ORG_ADMIN)
    plain = (await site.client.get("/admin/")).text
    asked = (await site.client.get("/admin/?bookings-today=bar&queue=line")).text

    assert 'role="img"' not in plain  # without a parameter every widget keeps its first view
    assert asked.count('role="img"') == 2
    assert 'class="chart__line"' in asked
    assert 'class="chart__bar chart-series-0"' in asked


async def test_the_switcher_links_keep_the_other_widgets_as_they_are(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (await site.client.get("/admin/?bookings-today=bar")).text

    assert 'aria-label="Weergave van Afspraken vandaag per groep"' in text
    assert (
        '<span class="widget__view widget__view--current" aria-current="true">Staaf</span>' in text
    )
    assert 'href="/admin/?bookings-today=pie"' in text
    assert 'href="/admin/"' in text  # back to the list: the default needs no parameter
    # Switching the queue keeps the bar on the bookings widget.
    assert 'href="/admin/?bookings-today=bar&amp;queue=line"' in text


async def test_a_widget_with_one_view_has_no_switcher(site: Site) -> None:
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/overview/w/changes")).text

    assert "widget__views" not in text
    assert "widget__views" not in (await site.client.get("/admin/overview/w/status")).text


async def test_unknown_or_unsupported_page_parameters_are_ignored(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (
        await site.client.get("/admin/?bookings-today=line&changes=pie&alerts=bar&queue=%3Cb%3E")
    ).text

    assert 'role="img"' not in text
    assert "&lt;b&gt;" not in text
    assert "<b>" not in text
    assert "alerts=" not in text


async def test_a_page_without_script_keeps_its_content_security_policy(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (await site.client.get("/admin/?bookings-today=pie&queue=bar")).text

    assert "<script" not in text
    assert "style=" not in text.split('class="widget-grid"')[1].split('id="groups-heading"')[0]


# --- helpers --------------------------------------------------------------------------------


def test_only_widgets_with_a_series_and_a_chart_view_draw_charts() -> None:
    for key in CHART_SERIES:
        item = widget(key)
        assert item is not None
        assert {View.BAR, View.LINE, View.PIE} & set(item.views), key
    for item in WIDGETS:
        if {View.BAR, View.LINE, View.PIE} & set(item.views) and item.available:
            assert item.key in CHART_SERIES, f"{item.key} declares a chart view without a series"


def test_the_page_address_leaves_out_widgets_at_their_default_view() -> None:
    bookings, queue = widget("bookings-today"), widget("queue")
    assert bookings is not None
    assert queue is not None
    on_page = {"bookings-today": bookings, "queue": queue}

    assert page_href({}, on_page) == "/admin/"
    assert page_href({"bookings-today": View.LIST, "queue": View.TABLE}, on_page) == "/admin/"
    assert page_href({"bookings-today": View.BAR}, on_page) == "/admin/?bookings-today=bar"
    assert page_href({"bookings-today": View.BAR, "queue": View.LINE}, on_page) == (
        "/admin/?bookings-today=bar&queue=line"
    )
    assert page_href({"nope": View.BAR}, on_page) == "/admin/"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"bookings-today": "pie"}, {"bookings-today": View.PIE}),
        ({"bookings-today": "line"}, {"bookings-today": View.LIST}),
        ({"changes": "bar"}, {}),  # a single-view widget takes no choice
        ({"alerts": "bar"}, {}),  # not on the page
        ({"queue": "<x>"}, {"queue": View.TABLE}),
        ({}, {}),
    ],
)
def test_the_views_the_address_asks_for(params: dict[str, str], expected: dict[str, View]) -> None:
    on_page = {k: w for k in ("bookings-today", "queue", "changes") if (w := widget(k)) is not None}

    assert explicit_views(on_page, params) == expected


async def test_a_group_name_with_markup_is_escaped_in_every_chart_text(site: Site) -> None:
    hostile = '"><script>alert(1)</script>'
    async with site_uow(site).locked() as tx:
        demo = await tx.get_group_summary("demo")
        assert demo is not None
        await tx.add_group(replace(demo.group, slug="hostile", name=hostile))
    await site.login_as(SUPER)
    for view in ("bar", "pie"):
        text = (await site.client.get(f"/admin/overview/w/bookings-today?view={view}")).text

        assert "<script>alert(1)</script>" not in text, view
        assert "&lt;script&gt;" in text, view
        assert 'aria-label=""' not in text, view
