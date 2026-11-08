"""``AlertSender`` adapters: email and a Webex space through a bot (ADR 0082 point 9).

- **Email** goes through the scope's mail channel (``MessageSender``, ADR 0009): an
  organisation's own SMTP settings, else the platform's fallback; the platform's targets use the
  fallback.
- **A Webex space** gets a markdown message (``POST /v1/messages``) from a **bot** the
  organisation (or the platform) created in the Webex developer portal and added to the space.
  The bot is its own Webex app with the scope ``spark:messages_write`` only; the Service App's
  token, which can create meetings and tasks, never gains a messaging scope. The bot token is
  sealed with the integration credentials (ADR 0008) under ``webex-bot:<organisation id>`` or
  ``webex-bot:platform``, write-only in the portal.

Every request to Webex is reported to the request log (ADR 0068) as method, path, status and
duration; a refusal never logs or keeps Webex's answer body. The bot's requests name no
platform, so a bot that is set up wrong never turns the Service App's Webex health
``degraded`` (ADR 0082 point 7).
"""

import time
from collections.abc import Callable
from datetime import datetime

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.credentials import Secrets
from app.adapters.db.record_names import bot_credential_name
from app.adapters.integrations import sealed_store
from app.adapters.webex_auth import http_failure
from app.config import Settings
from app.domain.alert_model import AlertChannelKind, AlertTarget, RenderedAlert
from app.domain.channels import ChannelKind
from app.domain.ports import (
    AlertSender,
    IntegrationError,
    MessageSender,
    OutboundMessage,
    RequestLog,
    RequestRecord,
    SenderDirectory,
)

log = structlog.get_logger()
MESSAGES_PATH = "/v1/messages"
TIMEOUT_SECONDS = 15.0


bot_name = bot_credential_name
"""The credential name of a bot token (defined with the organisation's delete)."""


class BotTokens:
    """The bot tokens of the platform and the organisations, sealed and write-only.

    Args:
        secrets: The encrypted credential store.
    """

    def __init__(self, secrets: Secrets) -> None:
        """Store the credential store."""
        self._secrets = secrets

    async def token(self, organisation_id: int | None) -> str | None:
        """Return the bot token of the scope, or ``None`` when none is set."""
        stored = await self._secrets.get(bot_name(organisation_id))
        token = stored.get("token") if stored else None
        return token if isinstance(token, str) else None

    async def has_token(self, organisation_id: int | None) -> bool:
        """Return whether the scope has a bot token (the portal shows only this)."""
        return await self.token(organisation_id) is not None

    async def save(self, organisation_id: int | None, token: str | None, now: datetime) -> None:
        """Store the scope's bot token; ``None`` removes the record."""
        if token is None:
            await self._secrets.delete(bot_name(organisation_id))
        else:
            await self._secrets.put(bot_name(organisation_id), {"token": token}, now)


class EmailAlertSender:
    """Sends an alert as a plain-text email through a mail channel.

    Args:
        sender: The scope's mail sender.
    """

    def __init__(self, sender: MessageSender) -> None:
        """Store the sender."""
        self._sender = sender

    async def send(self, address: str, alert: RenderedAlert) -> None:
        """Send ``alert`` to ``address``; a delivery failure becomes ``IntegrationError``."""
        try:
            await self._sender.send(
                OutboundMessage(ChannelKind.EMAIL, address, alert.subject, alert.text)
            )
        except IntegrationError:
            raise
        except Exception as exc:  # SMTP errors of every kind: retried as a failed delivery
            raise IntegrationError("mail_failed", retryable=True) from exc


class WebexSpaceSender:
    """Posts an alert to a Webex space with a bot token.

    Args:
        http: Shared HTTP client.
        token: The bot's access token.
        base_url: Webex API host, e.g. ``https://webexapis.com``.
        request_log: Where the request is reported (ADR 0068); ``None`` reports nothing.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        token: str,
        base_url: str,
        request_log: RequestLog | None = None,
    ) -> None:
        """Store the collaborators."""
        self._http = http
        self._token = token
        self._url = base_url.rstrip("/").removesuffix("/v1") + MESSAGES_PATH
        self._request_log = request_log

    async def send(self, address: str, alert: RenderedAlert) -> None:
        """Post ``alert`` to the room ``address``; refusals map to ``IntegrationError`` codes."""
        await self.post_markdown(address, alert.markdown)

    async def post_markdown(self, room_id: str, markdown: str) -> None:
        """Post ``markdown`` to ``room_id``; refusals map to ``IntegrationError`` codes."""
        started = time.monotonic()
        try:
            response = await self._http.post(
                self._url,
                json={"roomId": room_id, "markdown": markdown},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            await self._report(0, "unreachable", started, "")
            raise IntegrationError("unreachable", retryable=True) from exc
        tracking = response.headers.get("trackingid", "")
        if response.status_code in (200, 201):
            await self._report(response.status_code, "", started, tracking)
            return
        failure = http_failure(response)
        await self._report(response.status_code, failure.code, started, tracking)
        raise failure

    async def _report(self, status: int, error: str, started: float, tracking: str) -> None:
        """Report the request without its body (ADR 0068); never fails the send."""
        if self._request_log is None:
            return
        millis = int((time.monotonic() - started) * 1000)
        record = RequestRecord("POST", MESSAGES_PATH, status, error, tracking, millis)
        try:
            await self._request_log.record(record)
        except Exception as exc:  # the delivery is what counts; the log is best effort
            log.warning("request_log.failed", error=type(exc).__name__)


class ConfiguredAlertSenders:
    """Finds the sender of an alert target (``AlertSenders``).

    Args:
        senders: The organisations' message senders (their mail channel, else the fallback).
        fallback: Returns the platform's fallback mail sender, read at each send (portal
            changes apply live); the platform's email targets use it.
        bots: The bot tokens; ``None`` without a settings key (no secrets can be kept).
        http: Shared HTTP client.
        base_url: Webex API host.
        request_log: Where Webex requests are reported.
    """

    def __init__(
        self,
        senders: SenderDirectory,
        fallback: Callable[[], MessageSender | None],
        bots: BotTokens | None,
        http: httpx.AsyncClient,
        base_url: str,
        request_log: RequestLog | None = None,
    ) -> None:
        """Store the collaborators."""
        self._senders = senders
        self._fallback = fallback
        self._spaces = BotSpacePoster(bots, http, base_url, request_log)

    async def sender(self, target: AlertTarget) -> AlertSender | None:
        """Return the sender of ``target``, or ``None`` when its channel is not set up."""
        if target.kind is AlertChannelKind.EMAIL:
            mail = (
                self._fallback()
                if target.organisation_id is None
                else await self._senders.sender(target.organisation_id, ChannelKind.EMAIL)
            )
            return EmailAlertSender(mail) if mail is not None else None
        return await self._spaces.sender(target.organisation_id)


def alert_bots(engine: AsyncEngine, settings: Settings) -> BotTokens | None:
    """Return the bot tokens, or ``None`` without a settings key (nothing can be sealed)."""
    store = sealed_store(engine, settings)
    return BotTokens(store) if store is not None else None


class BotSpacePoster:
    """``SpacePoster`` for a group's team space: the organisation's bot (ADR 0082 point 12).

    Args:
        bots: The bot tokens; ``None`` without a settings key.
        http: Shared HTTP client.
        base_url: Webex API host.
        request_log: Where Webex requests are reported.
    """

    def __init__(
        self,
        bots: BotTokens | None,
        http: httpx.AsyncClient,
        base_url: str,
        request_log: RequestLog | None = None,
    ) -> None:
        """Store the collaborators."""
        self._bots = bots
        self._http = http
        self._base_url = base_url
        self._request_log = request_log

    async def sender(self, organisation_id: int | None) -> WebexSpaceSender | None:
        """Return the sender of the organisation's bot, or ``None`` without a bot token."""
        token = await self._bots.token(organisation_id) if self._bots else None
        if token is None:
            return None
        return WebexSpaceSender(self._http, token, self._base_url, self._request_log)

    async def post(self, organisation_id: int, room_id: str, markdown: str) -> None:
        """Post ``markdown`` to the room with the organisation's bot.

        Raises:
            IntegrationError: ``no_sender`` without a bot token, or the refusal's code.
        """
        sender = await self.sender(organisation_id)
        if sender is None:
            raise IntegrationError("no_sender", retryable=False)
        await sender.post_markdown(room_id, markdown)
