"""Response models of the machine API; they define the OpenAPI spec (ADR 0023).

No model has a field for personal data. Times are ISO 8601 in UTC, with the group's local date
and time next to them so a bot can speak them without converting time zones.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    """An access token (RFC 6749 §5.1)."""

    access_token: str = Field(description="Opaque bearer token; send as `Authorization: Bearer`.")
    token_type: str = Field("Bearer", description="Always `Bearer`.")
    expires_in: int = Field(description="Lifetime in seconds; request a new token after it.")
    scope: str = Field(description="Space-separated scopes of this token.")


class ErrorResponse(BaseModel):
    """Every refusal: a machine-readable code, never input or personal data."""

    error: str = Field(
        description="`invalid_request`, `invalid_client`, `invalid_scope`, `invalid_token`, "
        "`insufficient_scope`, `group_not_found`, `invalid_range`, `rate_limited`, "
        "`temporarily_unavailable` (the API is switched off), …"
    )


class ClientInfo(BaseModel):
    """Who the token belongs to and what it may reach."""

    client_id: str
    name: str
    organisation: str = Field(description="Slug of the client's organisation.")
    groups: list[str] | None = Field(
        description="Group slugs the client is narrowed to; `null` means every group of the "
        "organisation."
    )
    scopes: list[str] = Field(description="Scopes of this token.")
    token_expires_at: datetime


class GroupInfo(BaseModel):
    """A group (team) whose appointments the client may handle."""

    slug: str
    name: str
    timezone: str = Field(description="IANA time zone of the group, e.g. `Europe/Amsterdam`.")
    slot_minutes: int = Field(description="Length of one appointment.")


class SlotInfo(BaseModel):
    """A free appointment time."""

    start: datetime = Field(description="Start in UTC.")
    end: datetime = Field(description="End in UTC.")
    local_date: date = Field(description="Date in the group's time zone.")
    local_time: str = Field(description="Start time in the group's time zone, `HH:MM`.")
    label: str = Field(description="Date and time as text in the requested language.")
    remaining: int = Field(description="Places left at this time.")


class AvailabilityResponse(BaseModel):
    """The free times of one group between two local dates."""

    group: str
    timezone: str
    slots: list[SlotInfo]


class ConfigurationSummary(BaseModel):
    """What an imported configuration package held (ADR 0089): counts, never a value."""

    installation: str = Field(description="`INSTALLATION_NAME` of the source, or empty.")
    app_version: str = Field(description="Release of the source, or empty in development.")
    created_at: datetime = Field(description="When the package was exported.")
    revision: str = Field(description="Schema revision of the package.")
    counts: dict[str, int] = Field(
        description="Rows per table: `organisations`, `groups`, `identity_providers`, "
        "`api_clients`."
    )
    secrets: int = Field(description="How many sealed values the package carried.")


class PlanChange(BaseModel):
    """One object a configuration document would create, change or delete: no value."""

    action: str = Field(description="`create`, `update` or `delete`.")
    kind: str = Field(description="The kind of object, as the document names it.")
    path: str = Field(description="Where the object is in the document.")
    fields: list[str] = Field(description="The names of the fields written; none for a delete.")


class PlanRefusal(BaseModel):
    """One thing that stops a plan: where, why and which field, never a value."""

    path: str = Field(description="Where in the document the offending object is.")
    code: str = Field(description="The refusal code, for example `configuration_object_in_use`.")
    field: str | None = Field(description="The offending field, when the refusal is about one.")


class ConfigurationPlan(BaseModel):
    """What applying a configuration document would do (ADR 0099): names, never values."""

    ok: bool = Field(description="Whether the plan can be applied: nothing refuses it.")
    plan_hash: str = Field(description="Send it as `X-Plan-Hash` to apply exactly this plan.")
    changes: list[PlanChange]
    refusals: list[PlanRefusal]


class ConfigurationApplied(BaseModel):
    """What applying a configuration document did (ADR 0099)."""

    applied: bool = Field(description="Whether the plan was written; false when it was refused.")
    settings_version: int | None = Field(
        description="The new settings version, or null when nothing was written."
    )
    plan: ConfigurationPlan
