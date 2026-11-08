"""The record names integration secrets are sealed under (ADR 0008).

A record name is the associated data of the AES-GCM seal, so a value opens only under the name it
was sealed under. The statements that write a secret and the ones that read it back (the portal,
the worker, a configuration document) must build the name the same way; this module is their one
home, free of imports from the unit of work so every adapter can use it.
"""

from app.domain.channels import ChannelKind


def bot_credential_name(organisation_id: int | None) -> str:
    """Return the credential name of a Webex bot token: an organisation's or the platform's."""
    return f"webex-bot:{organisation_id if organisation_id is not None else 'platform'}"


def channel_secret_name(organisation_id: int, channel: ChannelKind) -> str:
    """Return the record name a channel secret is sealed under."""
    return f"channel:{organisation_id}:{channel.value}"
