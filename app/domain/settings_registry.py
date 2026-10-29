"""The settings registry (ADR 0042): every setting declared once, with its class and bounds.

Each entry names an environment variable (``key`` in lower case), its kind, its code default,
its class A–D, and for class C and D the bounds a superuser or org admin may set in the portal.
``app/config.py`` reads the environment; a test fails when a ``Settings`` field has no entry
here or its default differs, so the two never drift apart. ``docs/settings.md`` is generated
from this module (``python -m app.cli settings-doc``).

Portal bounds are stricter than what ``app/config.py`` accepts from the environment: an
environment lock outside them is accepted, so an existing ``.env`` keeps working, but it is
reported as outside the portal bounds (decided by the product owner on 2026-10-28).
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class SettingClass(StrEnum):
    """Where a setting lives and who changes it (ADR 0042 §1).

    Attributes:
        BOOTSTRAP: A, environment (or ``_FILE``) only; needed before the database or login.
        DEPLOYMENT: B, environment only; tied to the host, or a kill switch.
        PLATFORM: C, database, superuser; the environment may lock it.
        ORGANISATION: D, database, org admin; class C supplies the default.
    """

    BOOTSTRAP = "A"
    DEPLOYMENT = "B"
    PLATFORM = "C"
    ORGANISATION = "D"


class Kind(StrEnum):
    """The type of a setting's value, which decides its form field and validation."""

    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"
    STRING = "string"
    URL = "url"
    RATE = "rate"
    SECRET = "secret"  # noqa: S105  # nosec B105 - a kind name, not a password
    PATH = "path"


class Apply(StrEnum):
    """When a change takes effect: on the next request or loop, or after a restart (§6)."""

    LIVE = "live"
    RESTART = "restart"


class Topic(StrEnum):
    """The group a setting is shown under on the settings screens and in the docs."""

    BOOTSTRAP = "bootstrap"
    LOGIN = "login"
    HOST = "host"
    SWITCHES = "switches"
    DEVELOPMENT = "development"
    OPERATIONS = "operations"
    RETENTION = "retention"
    STORAGE = "storage"
    RATE_LIMITS = "rate_limits"
    ADMIN_SESSION = "admin_session"
    LOGIN_MAPPING = "login_mapping"
    MAIL = "mail"
    WEBEX = "webex"
    UPDATES = "updates"
    DISPLAY = "display"
    STAFF = "staff"
    PLANNER = "planner"


PLATFORM_VIEW = "settings.platform.view"
"""Permission to see *System status* and *Advanced settings* (superuser, auditor; §5)."""

PLATFORM_MANAGE = "settings.platform.manage"
"""Permission to change a class C setting (superuser only)."""

LOGIN_MANAGE = "settings.login.manage"
"""Permission to change the OIDC claim mapping (superuser only, never grantable; §5, §8)."""

ORGANISATION_MANAGE = "settings.organisation.manage"
"""Permission to change a class D setting (org admin for their own organisation, superuser)."""

ADVANCED_SETTINGS_TOPIC = "advanced-settings"
"""Manual topic (ADR 0033) of the *Advanced settings* screen."""

ORGANISATION_SETTINGS_TOPIC = "organisation-settings"
"""Manual topic of the organisation settings screen."""

CLOCK_STYLES = ("24h", "12h", "locale")
"""Values of ``TIME_FORMAT``; a test checks them against ``app.i18n.dates.ClockStyle``."""

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
"""Values of ``LOG_LEVEL``."""

LOG_LEVEL_MAX_EXPIRY_HOURS = 24
"""A log level set in the portal falls back to the default after at most this long (§6)."""

_RATE = re.compile(r"^\s*(\d+)\s*(?:/|per)\s*(second|minute|hour|day)s?\s*$")


@dataclass(frozen=True)
class SettingSpec:
    """One setting: its variable, kind, default, class and the bounds of the portal.

    Attributes:
        key: The environment variable in lower case, also the ``Settings`` field name.
        kind: The type of the value.
        default: The code default, as ``app/config.py`` has it (``None``: not set).
        class_: Where the setting lives (A–D).
        topic: The group it is shown under.
        description: One line for ``docs/settings.md``.
        minimum: Lowest value the portal accepts (number, or count for a rate).
        maximum: Highest value the portal accepts.
        choices: Allowed values of an enum.
        optional: Whether the setting may be left empty in the portal.
        https_only: Whether a URL must use ``https``.
        aliases: Other environment variables accepted for the same field.
        in_config: ``False`` for variables read by another container (the proxy).
    """

    key: str
    kind: Kind
    default: object
    class_: SettingClass
    topic: Topic
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    optional: bool = False
    https_only: bool = False
    aliases: tuple[str, ...] = ()
    in_config: bool = True

    @property
    def env(self) -> str:
        """The environment variable's name."""
        return self.key.upper()

    @property
    def secret(self) -> bool:
        """Whether the value is never shown (write-only in the portal; ADR 0008)."""
        return self.kind is Kind.SECRET

    @property
    def in_portal(self) -> bool:
        """Whether the portal holds the value: class C and D."""
        return self.class_ in (SettingClass.PLATFORM, SettingClass.ORGANISATION)

    @property
    def lockable(self) -> bool:
        """Whether an environment variable locks the portal value (class C and D)."""
        return self.in_portal

    @property
    def apply(self) -> Apply:
        """When a change applies: class C and D are always live (§6)."""
        return Apply.LIVE if self.in_portal else Apply.RESTART

    @property
    def permission(self) -> str | None:
        """The permission that changes this setting in the portal; ``None`` for A and B."""
        if self.class_ is SettingClass.ORGANISATION:
            return ORGANISATION_MANAGE
        if self.class_ is not SettingClass.PLATFORM:
            return None
        return LOGIN_MANAGE if self.topic is Topic.LOGIN_MAPPING else PLATFORM_MANAGE

    @property
    def label_key(self) -> str:
        """The i18n key of the field label."""
        return f"settings.field.{self.key}.label"

    @property
    def hint_key(self) -> str:
        """The i18n key of the hint under the field."""
        return f"settings.field.{self.key}.hint"

    @property
    def manual_topic(self) -> str | None:
        """The manual topic behind the field's (i), for settings shown in the portal."""
        if self.class_ is SettingClass.ORGANISATION:
            return ORGANISATION_SETTINGS_TOPIC
        return ADVANCED_SETTINGS_TOPIC if self.in_portal else None


def parse_rate(value: str) -> tuple[int, str] | None:
    """Return the count and period of a rate such as ``10/hour``; ``None`` if malformed."""
    match = _RATE.fullmatch(value)
    return (int(match.group(1)), match.group(2)) if match else None


def within_bounds(spec: SettingSpec, value: object) -> bool:
    """Whether ``value`` lies within the bounds the portal accepts for ``spec``.

    Settings without portal bounds (class A and B, strings, secrets) are always within them.
    """
    if value is None:
        return spec.optional or spec.default is None
    if spec.kind is Kind.RATE:
        return _rate_within(spec, str(value))
    if spec.kind is Kind.ENUM:
        return str(value) in spec.choices
    if spec.kind is Kind.URL and spec.https_only:
        return str(value).startswith("https://")
    if spec.kind in (Kind.INT, Kind.FLOAT) and isinstance(value, int | float):
        low = spec.minimum if spec.minimum is not None else value
        high = spec.maximum if spec.maximum is not None else value
        return low <= value <= high
    return True


def _rate_within(spec: SettingSpec, value: str) -> bool:
    """Whether a rate has the default's period and a count within the bounds."""
    parsed, default = parse_rate(value), parse_rate(str(spec.default))
    if parsed is None or default is None or parsed[1] != default[1]:
        return False
    low = spec.minimum if spec.minimum is not None else parsed[0]
    high = spec.maximum if spec.maximum is not None else parsed[0]
    return low <= parsed[0] <= high


def bounds_text(spec: SettingSpec) -> str:
    """Describe the portal bounds in one phrase for the docs and the CLI (``""``: none)."""
    if spec.choices:
        return ", ".join(spec.choices)
    if spec.kind is Kind.URL and spec.https_only:
        return "https only"
    if spec.minimum is None and spec.maximum is None:
        return ""
    period = parse_rate(str(spec.default)) if spec.kind is Kind.RATE else None
    low, high = _number(spec.minimum), _number(spec.maximum)
    text = f"{low}–{high}" if spec.maximum is not None else f"≥ {low}"
    text += f" per {period[1]}" if period else ""
    return f"empty or {text}" if spec.optional else text


def _number(value: float | None) -> str:
    """Write a bound without a needless ``.0``."""
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


_A, _B, _C, _D = (
    SettingClass.BOOTSTRAP,
    SettingClass.DEPLOYMENT,
    SettingClass.PLATFORM,
    SettingClass.ORGANISATION,
)


S, K, T = SettingSpec, Kind, Topic

REGISTRY: tuple[SettingSpec, ...] = (
    # A. Bootstrap: needed before the database or the login works.
    S(
        "environment",
        K.ENUM,
        "prod",
        _A,
        T.BOOTSTRAP,
        "Selects the production checks.",
        choices=("dev", "test", "prod"),
    ),
    S(
        "database_url",
        K.STRING,
        "sqlite+aiosqlite:///data/booking.db",
        _A,
        T.BOOTSTRAP,
        "The database the portal settings live in.",
    ),
    S(
        "settings_encryption_key",
        K.SECRET,
        None,
        _A,
        T.BOOTSTRAP,
        "Seals every secret in the database (ADR 0008).",
    ),
    S(
        "management_token_key",
        K.SECRET,
        None,
        _A,
        T.BOOTSTRAP,
        "Derives management tokens (ADR 0011); required in production.",
    ),
    S(
        "session_secret",
        K.SECRET,
        None,
        _A,
        T.BOOTSTRAP,
        "Signs admin sessions; required in production.",
    ),
    S(
        "oidc_discovery_url",
        K.URL,
        None,
        _A,
        T.LOGIN,
        "IdP discovery document; without it the admin portal is disabled.",
    ),
    S("oidc_client_id", K.STRING, "openplanner-admin", _A, T.LOGIN, "OIDC client of the portal."),
    S("oidc_client_secret", K.SECRET, None, _A, T.LOGIN, "OIDC client secret."),
    S(
        "oidc_scopes",
        K.STRING,
        "openid profile email",
        _A,
        T.LOGIN,
        "Scopes requested at login; wrong scopes break login.",
    ),
    # B. Deployment: tied to the host, DNS, proxy, image or healthcheck; kill switches.
    S(
        "public_base_url",
        K.URL,
        "http://127.0.0.1:8000",
        _B,
        T.HOST,
        "Base of every link and of the OIDC redirect; https in production.",
    ),
    S(
        "webhook_base_url",
        K.URL,
        None,
        _B,
        T.HOST,
        "Where Webex and WxCC deliver webhooks, if not PUBLIC_BASE_URL.",
    ),
    S(
        "forwarded_allow_ips",
        K.STRING,
        None,
        _B,
        T.HOST,
        "Address of the reverse proxy (read by uvicorn).",
        in_config=False,
    ),
    S(
        "tls_domain",
        K.STRING,
        None,
        _B,
        T.HOST,
        "Certificate domain (proxy.env, ADR 0030).",
        in_config=False,
    ),
    S(
        "acme_email",
        K.STRING,
        None,
        _B,
        T.HOST,
        "Mailbox for CA notices (proxy.env).",
        in_config=False,
    ),
    S("acme_ca", K.URL, None, _B, T.HOST, "ACME directory (proxy.env).", in_config=False),
    S(
        "worker_heartbeat_path",
        K.PATH,
        "data/worker.heartbeat",
        _B,
        T.HOST,
        "Heartbeat file the worker healthcheck reads.",
    ),
    S(
        "worker_heartbeat_max_age_seconds",
        K.FLOAT,
        120.0,
        _B,
        T.HOST,
        "Age after which the worker healthcheck fails.",
    ),
    S("app_version", K.STRING, "", _B, T.HOST, "Baked into release images (ADR 0029)."),
    S(
        "installation_name",
        K.STRING,
        "",
        _B,
        T.HOST,
        "Name of this installation in every log line and event record (ADR 0040/0034).",
    ),
    S(
        "webex_api_base_url",
        K.URL,
        "https://webexapis.com/v1",
        _B,
        T.HOST,
        "Webex API endpoint; changed only for test doubles.",
    ),
    S(
        "simulator_enabled",
        K.BOOL,
        False,
        _B,
        T.SWITCHES,
        "Platform-event simulator on admin booking pages; refused in production.",
    ),
    S(
        "agent_api_enabled",
        K.BOOL,
        False,
        _B,
        T.SWITCHES,
        "Machine API for contact-center bots; off means the routes do not exist (ADR 0023).",
    ),
    S(
        "rate_limit_enabled",
        K.BOOL,
        True,
        _B,
        T.SWITCHES,
        "Off switch for every rate limit; never in the portal.",
    ),
    S(
        "maintenance_mode",
        K.BOOL,
        False,
        _B,
        T.SWITCHES,
        "Maintenance mode from the host, also while the database is unavailable (ADR 0038); "
        "the portal cannot switch it off.",
    ),
    S(
        "dev_token_key_path",
        K.PATH,
        "data/dev-token.key",
        _B,
        T.DEVELOPMENT,
        "Development management-token key file.",
    ),
    S(
        "dev_session_key_path",
        K.PATH,
        "data/dev-session.key",
        _B,
        T.DEVELOPMENT,
        "Development session key file.",
    ),
    S(
        "dev_settings_key_path",
        K.PATH,
        "data/dev-settings.key",
        _B,
        T.DEVELOPMENT,
        "Development settings key file.",
    ),
    # C. Platform: Advanced settings; the environment may lock each one.
    S(
        "log_level",
        K.ENUM,
        "INFO",
        _C,
        T.OPERATIONS,
        "Log level; set in the portal it expires after at most 24 hours.",
        choices=LOG_LEVELS,
    ),
    S(
        "worker_poll_seconds",
        K.FLOAT,
        30.0,
        _C,
        T.OPERATIONS,
        "Interval of the worker loop.",
        minimum=5,
        maximum=300,
    ),
    S(
        "maintenance_cache_seconds",
        K.FLOAT,
        5.0,
        _C,
        T.OPERATIONS,
        "How long the web process trusts its last read of the maintenance switch (ADR 0038).",
        minimum=0,
        maximum=5,
    ),
    S(
        "maintenance_retry_after_seconds",
        K.INT,
        300,
        _C,
        T.OPERATIONS,
        "Retry-After of the 503 that citizens and the machine API get during maintenance "
        "(ADR 0038).",
        minimum=30,
        maximum=3600,
    ),
    S(
        "staff_last_seen_interval_seconds",
        K.INT,
        300,
        _C,
        T.STAFF,
        "An admin's last-seen time is written at most once per this interval (ADR 0046).",
        minimum=60,
        maximum=3600,
    ),
    S(
        "system_event_retention_days",
        K.INT,
        90,
        _C,
        T.RETENTION,
        "Days after which system events leave the audit log (ADR 0031).",
        minimum=30,
        maximum=3650,
    ),
    S(
        "audit_retention_days",
        K.INT,
        730,
        _C,
        T.RETENTION,
        "Days after which admin actions leave the audit log (ADR 0040).",
        minimum=365,
        maximum=3650,
    ),
    S(
        "security_event_retention_days",
        K.INT,
        365,
        _C,
        T.RETENTION,
        "Days after which security events leave the audit log (ADR 0040).",
        minimum=30,
        maximum=3650,
    ),
    S(
        "diagnostic_event_retention_days",
        K.INT,
        7,
        _C,
        T.RETENTION,
        "Days after which debug events of diagnostic mode are deleted (ADR 0040).",
        minimum=1,
        maximum=90,
    ),
    S(
        "diagnostic_mode_hours",
        K.INT,
        24,
        _C,
        T.OPERATIONS,
        "Hours after which diagnostic mode ends by itself (ADR 0040).",
        minimum=1,
        maximum=72,
    ),
    S(
        "security_event_window_minutes",
        K.INT,
        15,
        _C,
        T.OPERATIONS,
        "One security event per source and reason per window; repeats are counted (ADR 0034).",
        minimum=1,
        maximum=1440,
    ),
    S(
        "audit_export_max_rows",
        K.INT,
        100_000,
        _C,
        T.OPERATIONS,
        "Most rows one CSV export of the audit log may hold; more is refused (ADR 0040).",
        minimum=1_000,
        maximum=1_000_000,
    ),
    S(
        "audit_export_max_days",
        K.INT,
        366,
        _C,
        T.OPERATIONS,
        "Most days one CSV export of the audit log may cover (ADR 0040).",
        minimum=1,
        maximum=3660,
    ),
    S(
        "embedding_max_origins",
        K.INT,
        20,
        _C,
        T.OPERATIONS,
        "Most host websites one organisation may allow for the embedded widget (ADR 0035).",
        minimum=1,
        maximum=100,
    ),
    S(
        "storage_low_free_percent",
        K.FLOAT,
        15.0,
        _C,
        T.STORAGE,
        "Free space below which storage is low (ADR 0040).",
        minimum=1,
        maximum=50,
    ),
    S(
        "storage_critical_free_percent",
        K.FLOAT,
        5.0,
        _C,
        T.STORAGE,
        "Free space below which storage is critical; below the low limit.",
        minimum=1,
        maximum=50,
    ),
    S(
        "database_max_mb",
        K.INT,
        None,
        _C,
        T.STORAGE,
        "Database size above which storage is degraded; empty means no limit.",
        minimum=100,
        optional=True,
    ),
    S(
        "rate_limit_bookings",
        K.RATE,
        "10/hour",
        _C,
        T.RATE_LIMITS,
        "Bookings per IP address.",
        minimum=1,
        maximum=30,
    ),
    S(
        "rate_limit_reads",
        K.RATE,
        "60/minute",
        _C,
        T.RATE_LIMITS,
        "Availability reads per IP address.",
        minimum=10,
        maximum=300,
    ),
    S(
        "rate_limit_agent_tokens",
        K.RATE,
        "10/minute",
        _C,
        T.RATE_LIMITS,
        "Machine API token requests per client.",
        minimum=1,
        maximum=30,
    ),
    S(
        "rate_limit_agent_calls",
        K.RATE,
        "120/minute",
        _C,
        T.RATE_LIMITS,
        "Machine API calls per client.",
        minimum=10,
        maximum=600,
    ),
    S(
        "admin_session_hours",
        K.INT,
        8,
        _C,
        T.ADMIN_SESSION,
        "Lifetime of an admin session.",
        minimum=1,
        maximum=8,
    ),
    S(
        "admin_reauth_minutes",
        K.INT,
        60,
        _C,
        T.ADMIN_SESSION,
        "Maximum login age for a change (ADR 0015).",
        minimum=5,
        maximum=60,
    ),
    S("oidc_role_claim", K.STRING, "roles", _C, T.LOGIN_MAPPING, "Claim holding the roles."),
    S(
        "oidc_organisation_claim",
        K.STRING,
        "organisations",
        _C,
        T.LOGIN_MAPPING,
        "Claim holding the organisations.",
    ),
    S("oidc_group_claim", K.STRING, "groups", _C, T.LOGIN_MAPPING, "Claim holding the groups."),
    S(
        "oidc_role_superuser",
        K.STRING,
        "openplanner-superuser",
        _C,
        T.LOGIN_MAPPING,
        "Role value of a superuser.",
    ),
    S(
        "oidc_role_org_admin",
        K.STRING,
        "openplanner-org-admin",
        _C,
        T.LOGIN_MAPPING,
        "Role value of an organisation admin.",
    ),
    S(
        "oidc_role_group_admin",
        K.STRING,
        "openplanner-group-admin",
        _C,
        T.LOGIN_MAPPING,
        "Role value of a group admin.",
    ),
    S(
        "oidc_role_auditor",
        K.STRING,
        "openplanner-auditor",
        _C,
        T.LOGIN_MAPPING,
        "Role value of an auditor (ADR 0039).",
    ),
    S(
        "smtp_host",
        K.STRING,
        None,
        _C,
        T.MAIL,
        "Fallback mail server (ADR 0022); empty means no fallback mail.",
        optional=True,
    ),
    S(
        "smtp_port",
        K.INT,
        25,
        _C,
        T.MAIL,
        "Port of the fallback mail server.",
        minimum=1,
        maximum=65535,
    ),
    S("smtp_starttls", K.BOOL, False, _C, T.MAIL, "Upgrade the connection with STARTTLS."),
    S("smtp_tls", K.BOOL, False, _C, T.MAIL, "Implicit TLS (also on port 465)."),
    S(
        "smtp_username",
        K.STRING,
        None,
        _C,
        T.MAIL,
        "Login; needs STARTTLS or TLS.",
        optional=True,
        aliases=("smtp_user",),
    ),
    S(
        "smtp_password",
        K.SECRET,
        None,
        _C,
        T.MAIL,
        "Password of the login; write-only.",
        optional=True,
        aliases=("smtp_user_password",),
    ),
    S(
        "mail_from",
        K.STRING,
        "OpenPlanner <noreply@openplanner.invalid>",
        _C,
        T.MAIL,
        "Fallback sender.",
    ),
    S(
        "webex_client_id",
        K.STRING,
        None,
        _C,
        T.WEBEX,
        "Service App (ADR 0017/0018); moves to the organisation with ADR 0032.",
        optional=True,
    ),
    S(
        "webex_client_secret",
        K.SECRET,
        None,
        _C,
        T.WEBEX,
        "Service App secret; write-only.",
        optional=True,
    ),
    S(
        "webex_refresh_token",
        K.SECRET,
        None,
        _C,
        T.WEBEX,
        "Bootstraps the token store; write-only.",
        optional=True,
    ),
    S(
        "webex_webhook_secret",
        K.SECRET,
        None,
        _C,
        T.WEBEX,
        "Webex webhook secret; to be generated and rotated by the server.",
        optional=True,
    ),
    S(
        "wxcc_api_base_url",
        K.URL,
        None,
        _C,
        T.WEBEX,
        "WxCC API host of the region.",
        https_only=True,
        optional=True,
    ),
    S(
        "wxcc_webhook_secret",
        K.SECRET,
        None,
        _C,
        T.WEBEX,
        "WxCC webhook secret; to be generated and rotated by the server.",
        optional=True,
    ),
    S(
        "update_repository",
        K.STRING,
        None,
        _C,
        T.UPDATES,
        "owner/name of the release repository (ADR 0029).",
        optional=True,
    ),
    S(
        "update_check_token",
        K.SECRET,
        None,
        _C,
        T.UPDATES,
        "Read token for a private repository; write-only.",
        optional=True,
    ),
    # D. Organisation: the value per organisation; the superuser's or the environment's value
    # is the default for organisations without one.
    S(
        "time_format",
        K.ENUM,
        "24h",
        _D,
        T.DISPLAY,
        "How clock times are written.",
        choices=CLOCK_STYLES,
    ),
    S(
        "admin_timezone",
        K.STRING,
        "Europe/Amsterdam",
        _D,
        T.DISPLAY,
        "IANA time zone of the admin portal.",
    ),
    S(
        "staff_dormant_after_days",
        K.INT,
        90,
        _D,
        T.STAFF,
        "Days without a login after which a person is marked dormant in the staff list (ADR 0046).",
        minimum=14,
        maximum=365,
    ),
    S(
        "shortfall_horizon_days",
        K.INT,
        14,
        _D,
        T.PLANNER,
        "Days ahead the shortfall notice on group pages and the dashboard looks (ADR 0044).",
        minimum=1,
        maximum=60,
    ),
)
"""Every setting of the service, in the order of ``docs/settings.md``."""

BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in REGISTRY}
"""The registry by key."""


def lookup(name: str) -> SettingSpec | None:
    """Return the setting for a key or variable name (any case, aliases included)."""
    wanted = name.strip().lower()
    return BY_KEY.get(wanted) or next((s for s in REGISTRY if wanted in s.aliases), None)
