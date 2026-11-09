"""The usage API of the machine API: scope, grant, small cells, formats, paging (ADR 0117)."""

import csv
import io
import json

from app.domain.usage_api_service import UsageApiLimits
from tests.support.agent_api import BASE, Agent
from tests.support.domain import NOW
from tests.support.usage_data import DAY, fill_app

URL = f"{BASE}/reports/usage?from={DAY}&to={DAY}"
READ = {"scopes": ["reports:read"]}


async def _agent(agent: Agent, count: int = 12, **overrides: object) -> dict[str, str]:
    await fill_app(agent.app, agent.app.state.clock, "demo", count)
    issued = await agent.register(**{**READ, **overrides})
    return await agent.bearer(issued)


async def test_a_client_with_the_scope_reads_the_hourly_figures(agent: Agent) -> None:
    headers = await _agent(agent)

    response = await agent.client.get(URL, headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private"
    assert response.headers["etag"].startswith('"')
    body = response.json()
    assert body["schema"] == "openplanner.usage/1"
    assert body["next_cursor"] is None
    row = next(r for r in body["rows"] if r["metric"] == "U1")
    assert (row["hour"], row["organisation"], row["group"], row["count"]) == (
        "2026-01-01T08:00:00Z",
        "demo",
        "demo",
        12,
    )


async def test_a_client_without_the_scope_is_refused(agent: Agent) -> None:
    await fill_app(agent.app, agent.app.state.clock, "demo", 12)
    headers = await agent.bearer(await agent.register())

    response = await agent.client.get(URL, headers=headers)

    assert response.status_code == 403
    assert response.json() == {"error": "insufficient_scope"}


async def test_without_a_token_there_is_no_answer(agent: Agent) -> None:
    assert (await agent.client.get(URL)).status_code == 401


async def test_a_cell_under_the_threshold_is_not_returned(agent: Agent) -> None:
    headers = await _agent(agent, count=3)

    response = await agent.client.get(URL, headers=headers)

    assert response.json()["rows"] == []


async def test_the_etag_answers_not_modified_until_something_changes(agent: Agent) -> None:
    headers = await _agent(agent)
    first = await agent.client.get(URL, headers=headers)

    again = await agent.client.get(URL, headers={**headers, "if-none-match": first.headers["etag"]})
    other = await agent.client.get(
        f"{URL}&metric=U1", headers={**headers, "if-none-match": first.headers["etag"]}
    )

    assert again.status_code == 304
    assert other.status_code == 200


async def test_a_client_narrowed_to_other_groups_cannot_read_a_foreign_one(agent: Agent) -> None:
    headers = await _agent(agent, groups=["demo"])

    ok = await agent.client.get(f"{URL}&group=demo", headers=headers)
    foreign = await agent.client.get(f"{URL}&group=andere-team", headers=headers)
    unknown = await agent.client.get(f"{URL}&group=nope", headers=headers)

    assert ok.status_code == 200
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json() == {"error": "group_not_found"}


async def test_csv_and_ndjson_carry_the_same_rows(agent: Agent) -> None:
    headers = await _agent(agent)

    as_csv = await agent.client.get(f"{URL}&format=csv", headers=headers)
    as_ndjson = await agent.client.get(f"{URL}&format=ndjson", headers=headers)
    as_json = await agent.client.get(URL, headers=headers)

    table = list(csv.reader(io.StringIO(as_csv.text[1:], newline=""), delimiter=";"))
    lines = [json.loads(line) for line in as_ndjson.text.splitlines()]
    assert as_csv.headers["content-type"].startswith("text/csv")
    assert as_ndjson.headers["content-type"].startswith("application/x-ndjson")
    assert len(table) - 1 == len(lines) == len(as_json.json()["rows"])
    assert table[0][:4] == ["hour", "organisation", "group", "metric"]


async def test_pages_follow_the_cursor_and_end(agent: Agent) -> None:
    headers = await _agent(agent)
    agent.app.state.usage_api_service._limits = lambda: UsageApiLimits(1, 400)  # noqa: SLF001
    seen: list[str] = []
    url = URL

    for _ in range(10):
        page = (await agent.client.get(url, headers=headers)).json()
        seen += [r["metric"] for r in page["rows"]]
        if page["next_cursor"] is None:
            break
        url = f"{URL}&cursor={page['next_cursor']}"

    assert len(seen) == len(set(seen)) >= 2
    assert page["next_cursor"] is None


async def test_a_cut_keeps_that_column_and_adds_up_the_rest(agent: Agent) -> None:
    headers = await _agent(agent)

    rows = (await agent.client.get(f"{URL}&metric=U2&cut=outcome", headers=headers)).json()["rows"]

    assert [(r["outcome"], r["count"]) for r in rows] == [("cancelled_by_planner", 12)]


async def test_bad_parameters_are_refused_without_an_echo(agent: Agent) -> None:
    headers = await _agent(agent)

    for sent in (
        f"{URL}&metric=Z9",
        f"{URL}&cut=secret",
        f"{URL}&cursor=%%%",
        f"{BASE}/reports/usage?from=2026-01-05&to=2026-01-01",
    ):
        response = await agent.client.get(sent, headers=headers)
        assert response.status_code in (400, 422), sent
        assert "secret" not in response.text


async def test_the_range_is_limited_by_the_setting(agent: Agent) -> None:
    headers = await _agent(agent)
    agent.app.state.usage_api_service._limits = lambda: UsageApiLimits(1000, 31)  # noqa: SLF001

    response = await agent.client.get(
        f"{BASE}/reports/usage?from=2025-11-01&to=2026-01-01", headers=headers
    )

    assert response.status_code == 422
    assert NOW.year == 2026


async def test_a_team_metric_is_never_served_even_unfiltered(agent: Agent) -> None:
    headers = await _agent(agent)
    service = agent.app.state.usage_api_service
    original = service._uow  # noqa: SLF001
    from app.domain.usage import METRICS  # noqa: PLC0415

    staff = next(m.id for m in METRICS if m.staff)
    async with original.locked() as tx:
        from app.domain.usage import UsageCell  # noqa: PLC0415

        await tx.add_usage_cells([UsageCell(NOW.replace(minute=0), 1, 1, staff, 9, 0, 0)])

    rows = (await agent.client.get(URL, headers=headers)).json()["rows"]

    assert staff not in {r["metric"] for r in rows}
