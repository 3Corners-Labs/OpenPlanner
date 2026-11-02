"""``InstanceProbe`` on ``httpx`` (ADR 0086).

Requests ``{origin}/api/v1/instance?probe={nonce}`` the way a participant's browser would: the
host's CA bundle, no redirect followed, no cookie, no credentials. A redirect is a finding of
its own (the address answers with another name), so it is never followed. The reply is parsed
into the domain's ``InstanceReply``; a failure becomes an error word (the network's or the
provider's reason, ADR 0065 style), never the body and never a stack trace.
"""

import ssl
from typing import Any

import httpx
import structlog

from app.domain.public_address import INSTANCE_PATH, Forwarded, InstanceAnswer, InstanceReply

log = structlog.get_logger()

TIMEOUT_SECONDS = 10.0
"""How long the request may take in all; fixed (ADR 0064, *Fixed on purpose*, same cycle)."""

_ID_CHARS = 64
"""Longest instance id read from an answer; a UUID is 36."""


class HttpxInstanceProbe:
    """Asks an address for the instance endpoint.

    Args:
        timeout: Seconds for the whole request.
        transport: A transport to use instead of the network (tests).
    """

    def __init__(
        self,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Store the options."""
        self._timeout = timeout
        self._transport = transport

    async def probe(self, url: str, nonce: str) -> InstanceReply:
        """Return what ``url`` answered; never raises."""
        target = f"{url.rstrip('/')}{INSTANCE_PATH}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = await client.get(target, params={"probe": nonce})
        except httpx.TimeoutException:
            return InstanceReply(error="timeout")
        except httpx.ConnectError as exc:
            return InstanceReply(error="tls" if _is_tls(exc) else "unreachable")
        except httpx.HTTPError as exc:
            log.warning("address.probe_failed", error=type(exc).__name__)
            return InstanceReply(error="unreachable")
        return _reply(response)


def _is_tls(exc: httpx.ConnectError) -> bool:
    """Return whether a connection failed in the TLS handshake (certificate or protocol)."""
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _reply(response: httpx.Response) -> InstanceReply:
    """Parse the response; redirects, other statuses and unreadable bodies are named."""
    if response.is_redirect:
        return InstanceReply(redirected=True)
    if response.status_code != httpx.codes.OK:
        return InstanceReply(error=f"http_{response.status_code}")
    try:
        body: Any = response.json()
    except ValueError:
        return InstanceReply(error="unreadable")
    answer = _answer(body)
    return InstanceReply(answer) if answer else InstanceReply(error="unreadable")


def _answer(body: Any) -> InstanceAnswer | None:
    """Read the answer's fields; ``None`` when the instance id is missing or not a string."""
    if not isinstance(body, dict):
        return None
    instance_id = body.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id or len(instance_id) > _ID_CHARS:
        return None
    scheme = body.get("scheme")
    forwarded = body.get("forwarded")
    try:
        state = Forwarded(forwarded) if isinstance(forwarded, str) else None
    except ValueError:
        state = None
    return InstanceAnswer(instance_id, scheme if scheme in {"http", "https"} else None, state)
