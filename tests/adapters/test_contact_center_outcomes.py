"""What the adapters tell the contact-center attempts (ADR 0121 step 6b).

Whether a failed request may have reached the platform (``IntegrationError.ambiguous``): only
then is a create reconciled instead of retried. And the origin of a task event, by which a late
task is matched to its attempt: only the id, never the name next to it.
"""

import httpx
import pytest

from app.adapters.webhooks import parse_wxcc
from app.domain.ports import IntegrationError
from tests.fakes.tokens import authorized_client

URL = "https://api.wxcc-eu1.cisco.com/v2/tasks"


async def _failure(handler: object) -> IntegrationError:
    """Return the error of one POST through the authorised client to ``handler``."""
    client = authorized_client(handler)  # type: ignore[arg-type]
    with pytest.raises(IntegrationError) as excinfo:
        await client.request("POST", URL, json={}, ok=(201,))
    return excinfo.value


@pytest.mark.parametrize(
    ("error", "ambiguous"),
    [
        (httpx.ConnectError("refused"), False),
        (httpx.ConnectTimeout("no route"), False),
        (httpx.ReadTimeout("no answer"), True),
        (httpx.RemoteProtocolError("cut off"), True),
    ],
)
async def test_a_transport_error_after_sending_is_ambiguous(
    error: Exception, ambiguous: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tasks"):
            raise error
        return httpx.Response(200, json={})

    failure = await _failure(handler)

    assert (failure.code, failure.retryable, failure.ambiguous) == ("unreachable", True, ambiguous)


@pytest.mark.parametrize(("status", "ambiguous"), [(504, True), (503, False), (400, False)])
async def test_only_a_gateway_timeout_answer_is_ambiguous(status: int, ambiguous: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tasks"):
            return httpx.Response(status, json={})
        return httpx.Response(200, json={})

    assert (await _failure(handler)).ambiguous is ambiguous


def _event(data: dict[str, object]) -> dict[str, object]:
    return {"type": "task:new", "id": "e-1", "data": {"taskId": "t-1", **data}}


def test_the_origin_is_the_id_and_never_the_name() -> None:
    sent = parse_wxcc(_event({"origin": {"id": "OP-000123", "name": "Ada"}}))
    plain = parse_wxcc(_event({"origin": "OP-000124"}))
    without = parse_wxcc(_event({}))
    odd = parse_wxcc(_event({"origin": {"name": "Ada"}}))

    assert sent is not None
    assert sent.origin == "OP-000123"
    assert "Ada" not in repr(sent)
    assert plain is not None
    assert plain.origin == "OP-000124"
    assert without is not None
    assert without.origin == ""
    assert odd is not None
    assert odd.origin == ""
