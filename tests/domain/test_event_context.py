import asyncio

from app.domain.event_context import (
    TRACE_ID,
    bind_level,
    current,
    current_service,
    current_trace_id,
    set_service,
    trace_scope,
)


def test_outside_a_scope_there_is_no_trace() -> None:
    assert current_trace_id() == ""
    assert current().organisation is None


def test_a_scope_has_a_fresh_trace_id_and_restores_the_previous_one() -> None:
    with trace_scope() as outer:
        bind_level("org", "grp")
        with trace_scope() as inner:
            assert TRACE_ID.fullmatch(inner)
            assert inner != outer
            assert current().group is None
        assert current().trace_id == outer
        assert (current().organisation, current().group) == ("org", "grp")
    assert current_trace_id() == ""
    assert current().group is None


def test_service_is_one_value_per_process() -> None:
    set_service("web")
    try:
        assert current_service() == "web"
        assert current().service == "web"
    finally:
        set_service("")


async def test_concurrent_tasks_do_not_see_each_others_level() -> None:
    async def handle(group: str) -> tuple[str, str | None]:
        with trace_scope() as trace_id:
            bind_level("org", group)
            await asyncio.sleep(0)
            return trace_id, current().group

    first, second = await asyncio.gather(handle("a"), handle("b"))

    assert first[1] == "a"
    assert second[1] == "b"
    assert first[0] != second[0]
