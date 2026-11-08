"""Signed Webex Contact Center webhook bodies for the webhook adapter and endpoint tests."""

import hashlib
import hmac
import json
from datetime import UTC, datetime

SECRET = "webhook-secret"
"""The webhook secret that WxCC and the application share in the tests."""
NOW = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
"""When a webhook is sent unless a test says otherwise: Monday 5 January 09:00 Amsterdam."""


def wxcc_body(event_type: str = "task:connected", at: datetime = NOW, **data: object) -> bytes:
    """Return the body of a WxCC event of ``event_type`` on task ``task-1``, sent ``at``."""
    ms = int(at.timestamp() * 1000)
    return json.dumps(
        {
            "id": "4c2c4890",
            "specversion": "1.0",
            "type": event_type,
            "comciscotimestamp": ms,
            "data": {"taskId": "task-1", **data},
        }
    ).encode()


def sign(body: bytes, algorithm: str = "sha256") -> str:
    """Return the hex HMAC of ``body`` under ``SECRET``, as WxCC signs it."""
    return hmac.new(SECRET.encode(), body, getattr(hashlib, algorithm)).hexdigest()


def timestamp(body: bytes) -> str:
    """Return the timestamp WxCC sends with ``body``: its event time in milliseconds."""
    return str(json.loads(body)["comciscotimestamp"])
