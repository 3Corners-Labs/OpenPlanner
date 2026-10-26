"""``MessageSender`` for SMS provider ``webex_connect`` (HANDOVER B6, ADR 0022, 0028).

One request per message to the SMS API v1 of the organisation's Connect tenant:

    POST {api_base_url}/v1/sms/messages        header  key: <API key>
    {"from": sender_id, "to": "+316…", "content": text, "contentType": "text" | "unicode",
     "correlationId": "op-<uuid>"}             → 201 (empty body)

The body carries the phone number and the message text only; the text holds first name,
date, time, team and the management link, nothing else (ADR 0009). Response bodies are never
logged or raised: they could echo the number back.
"""

import uuid
from typing import Any

import httpx
import structlog

from app.adapters.sms import SmsGatewayConfig, fits_gsm_7bit
from app.adapters.webex_auth import http_failure
from app.domain.channel_settings import e164
from app.domain.channels import ChannelKind
from app.domain.ports import IntegrationError, OutboundMessage

log = structlog.get_logger()

SMS_PATH = "/v1/sms/messages"


def content_type(text: str) -> str:
    """Return ``text`` when every character fits GSM 7-bit, else ``unicode``."""
    return "text" if fits_gsm_7bit(text) else "unicode"


def sms_body(
    message: OutboundMessage, config: SmsGatewayConfig, correlation_id: str
) -> dict[str, Any]:
    """Return the SMS API v1 request for ``message``."""
    return {
        "from": config.sender_id,
        "to": e164(message.recipient, config.country_code),
        "content": message.text,
        "contentType": content_type(message.text),
        "correlationId": correlation_id,
    }


class WebexConnectSmsSender:
    """Sends SMS messages through one Webex Connect service.

    Args:
        config: The organisation's SMS settings; the API key is the Connect service key.
        http: Shared HTTP client (timeouts are set there).
    """

    def __init__(self, config: SmsGatewayConfig, http: httpx.AsyncClient) -> None:
        """Store the collaborators."""
        self._config = config
        self._http = http
        self._url = config.api_base_url.rstrip("/") + SMS_PATH

    async def send(self, message: OutboundMessage) -> None:
        """Deliver an SMS.

        Raises:
            ValueError: If the message is not for the SMS channel.
            IntegrationError: ``unreachable``, ``rate_limited``, ``server_error`` (retried) or
                ``unauthorized`` / ``rejected_<status>`` (failed at once).
        """
        if message.channel is not ChannelKind.SMS:
            raise ValueError("WebexConnectSmsSender only sends SMS")
        correlation_id = f"op-{uuid.uuid4()}"
        try:
            response = await self._http.post(
                self._url,
                json=sms_body(message, self._config, correlation_id),
                headers={"key": self._config.api_key},
            )
        except httpx.HTTPError as exc:
            log.warning("integration.unreachable", error=type(exc).__name__)
            raise IntegrationError("unreachable", retryable=True) from exc
        if response.status_code not in (200, 201, 202):
            raise http_failure(response)
        log.info("sms.accepted", correlation_id=correlation_id)
