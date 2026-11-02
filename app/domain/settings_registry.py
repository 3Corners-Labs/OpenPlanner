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

import phonenumbers

from app.domain.authorization import Permission


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
    BOOKING_FORM = "booking_form"
    CONFIRMATION = "confirmation"
    PARTICIPANT_LINK = "participant_link"


PLATFORM_VIEW = Permission.SETTINGS_PLATFORM_VIEW
"""Permission to see *System status* and *Advanced settings* (superuser, auditor; §5)."""

PLATFORM_MANAGE = Permission.SETTINGS_PLATFORM_MANAGE
"""Permission to change a class C setting (superuser only)."""

LOGIN_MANAGE = Permission.SETTINGS_LOGIN_MANAGE
"""Permission to change the OIDC claim mapping (superuser only, never grantable; §5, §8)."""

ORGANISATION_MANAGE = Permission.SETTINGS_ORGANISATION_MANAGE
"""Permission to change a class D setting (org admin for their own organisation, superuser)."""

ADVANCED_SETTINGS_TOPIC = "advanced-settings"
"""Manual topic (ADR 0033) of the *Advanced settings* screen."""

ORGANISATION_SETTINGS_TOPIC = "organisation-settings"
"""Manual topic of the organisation settings screen."""

CLOCK_STYLES = ("24h", "12h", "locale")
"""Values of ``TIME_FORMAT``; a test checks them against ``app.i18n.dates.ClockStyle``."""

PHONE_REGIONS = tuple(sorted(phonenumbers.SUPPORTED_REGIONS))
"""Values of ``PHONE_DEFAULT_REGION``: the regions ``phonenumbers`` knows (ADR 0055)."""

LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
"""Values of ``LOG_LEVEL``."""

CLAIM_MODES = ("legacy", "shadow", "entitlements")
"""Values of ``OIDC_CLAIM_MODE`` (ADR 0049 point 13); a test checks them against ``ClaimMode``."""

LOG_LEVEL_MAX_EXPIRY_HOURS = 24
"""A log level set in the portal falls back to the default after at most this long (§6).
``TRACE`` falls back after at most ``TRACE_LEVEL_MAX_HOURS`` (ADR 0085 point 8)."""

TRACE_LEVEL = "TRACE"
"""The level with the shorter cap."""

TRACE_LEVEL_MAX_HOURS = "trace_level_max_hours"
"""Key of that cap."""

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
    def permission(self) -> Permission | None:
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
        "settings_encryption_key_previous",
        K.SECRET,
        None,
        _A,
        T.BOOTSTRAP,
        "The key before a rotation (at least 32 bytes); sealed values under it stay readable "
        "until `rekey` has run. Empty outside a rotation (ADR 0094).",
    ),
    S(
        "management_token_key_previous",
        K.SECRET,
        None,
        _A,
        T.BOOTSTRAP,
        "The token key before a rotation (at least 32 bytes); links of bookings made under it "
        "keep working. Empty outside a rotation (ADR 0094).",
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
    S(
        "oidc_issuer",
        K.URL,
        None,
        _B,
        T.LOGIN,
        "Issuer the IdP announces, when it differs from the discovery URL without its suffix.",
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
    S(
        "admin_mfa_required",
        K.BOOL,
        False,
        _B,
        T.LOGIN,
        "Refuse an admin login without a second factor (ADR 0100); on for the bundled Keycloak.",
    ),
    S(
        "oidc_acr_values",
        K.STRING,
        "mfa",
        _B,
        T.LOGIN,
        "acr_values requested at login; an acr from this list counts as proof of MFA (ADR 0100).",
    ),
    S(
        "oidc_mfa_amr_values",
        K.STRING,
        "hwk mfa otp swk",
        _B,
        T.LOGIN,
        "amr values that count as proof of MFA (RFC 8176), space separated (ADR 0100).",
    ),
    S(
        "oidc_mfa_acr_values",
        K.STRING,
        "",
        _B,
        T.LOGIN,
        "acr values that count as proof of MFA; empty: the values asked for (ADR 0100).",
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
        "Address of the reverse proxy (read by uvicorn). Other than the bundled proxy's, "
        "System status warns unless web serves TLS (ADR 0091).",
    ),
    S(
        "web_tls_cert_file",
        K.PATH,
        None,
        _B,
        T.HOST,
        "Certificate chain (PEM) for uvicorn, for an own reverse proxy on another machine; with "
        "WEB_TLS_KEY_FILE web serves HTTPS (ADR 0091).",
    ),
    S(
        "web_tls_key_file",
        K.PATH,
        None,
        _B,
        T.HOST,
        "Private key (PEM) of WEB_TLS_CERT_FILE; readable by the web user only (ADR 0091).",
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
        "tls_cert_file",
        K.PATH,
        None,
        _B,
        T.HOST,
        "Own certificate chain (PEM) for the proxy; with TLS_KEY_FILE it replaces ACME "
        "(proxy.env, ADR 0064).",
        in_config=False,
    ),
    S(
        "tls_key_file",
        K.PATH,
        None,
        _B,
        T.HOST,
        "Private key of the own certificate (proxy.env, ADR 0064).",
        in_config=False,
    ),
    S(
        "tls_proxy_bundled",
        K.BOOL,
        False,
        _B,
        T.HOST,
        "The bundled HTTPS proxy runs, so the portal manages the certificate source "
        "(docker-compose.yml sets it from OPENPLANNER_TLS; ADR 0072).",
    ),
    S(
        "tls_portal_certificate_dir",
        K.PATH,
        "portal-certs",
        _B,
        T.HOST,
        "The volume shared with the proxy that holds the own certificate's private key, its "
        "request and the served pair; never under data/ (ADR 0072).",
    ),
    S(
        "tls_portal_poll_seconds",
        K.INT,
        10,
        _B,
        T.HOST,
        "How often the proxy looks for a changed certificate from the portal (proxy.env, "
        "ADR 0072).",
        minimum=2,
        maximum=300,
        in_config=False,
    ),
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
    S("app_commit", K.STRING, "", _B, T.HOST, "Git commit baked into every image (ADR 0052)."),
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
        "Log level; set in the portal it expires after at most 24 hours (TRACE: after at most "
        "TRACE_LEVEL_MAX_HOURS).",
        choices=LOG_LEVELS,
    ),
    S(
        "trace_level_max_hours",
        K.INT,
        4,
        _C,
        T.OPERATIONS,
        "Hours after which LOG_LEVEL=TRACE set in the portal falls back (ADR 0085).",
        minimum=1,
        maximum=24,
    ),
    S(
        "log_ship_batch_size",
        K.INT,
        100,
        _C,
        T.OPERATIONS,
        "Lines a log destination receives in one delivery at most (ADR 0085).",
        minimum=1,
        maximum=1000,
    ),
    S(
        "log_ship_flush_seconds",
        K.INT,
        2,
        _C,
        T.OPERATIONS,
        "Seconds a log destination waits to fill a delivery (ADR 0085).",
        minimum=1,
        maximum=60,
    ),
    S(
        "log_ship_timeout_seconds",
        K.INT,
        5,
        _C,
        T.OPERATIONS,
        "Seconds a delivery to a log destination may take to connect and send (ADR 0085).",
        minimum=1,
        maximum=30,
    ),
    S(
        "log_ship_queue_size",
        K.INT,
        10_000,
        _C,
        T.OPERATIONS,
        "Lines each process keeps per log destination; the oldest go when full (ADR 0085).",
        minimum=1_000,
        maximum=100_000,
    ),
    S(
        "log_ship_backoff_max_seconds",
        K.INT,
        300,
        _C,
        T.OPERATIONS,
        "Longest wait between two attempts to reach a failing log destination (ADR 0085).",
        minimum=10,
        maximum=3600,
    ),
    S(
        "sql_slow_ms",
        K.INT,
        200,
        _C,
        T.OPERATIONS,
        "A statement slower than this many milliseconds leaves a sql.slow trace line (ADR 0085).",
        minimum=10,
        maximum=10_000,
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
        "Retry-After of the 503 that participants and the machine API get during maintenance "
        "(ADR 0038).",
        minimum=30,
        maximum=3600,
    ),
    S(
        "maintenance_alert_hours",
        K.INT,
        4,
        _C,
        T.OPERATIONS,
        "Maintenance on longer than this is reported as a system event (ADR 0038).",
        minimum=1,
        maximum=72,
    ),
    S(
        "maintenance_alert_repeat_hours",
        K.INT,
        24,
        _C,
        T.OPERATIONS,
        "The overdue maintenance event repeats at this interval while it lasts (ADR 0038).",
        minimum=1,
        maximum=168,
    ),
    S(
        "tls_check_interval_hours",
        K.INT,
        6,
        _C,
        T.OPERATIONS,
        "How often the worker probes the certificate of the public address (ADR 0064).",
        minimum=1,
        maximum=168,
    ),
    S(
        "tls_expiry_warning_days",
        K.INT,
        20,
        _C,
        T.OPERATIONS,
        "Fewer days left than this makes the certificate finding 'expiring' (ADR 0064).",
        minimum=1,
        maximum=60,
    ),
    S(
        "tls_alert_repeat_hours",
        K.INT,
        24,
        _C,
        T.OPERATIONS,
        "A lasting certificate finding is reported again at this interval (ADR 0064).",
        minimum=1,
        maximum=168,
    ),
    S(
        "tls_certificate_source",
        K.ENUM,
        "acme",
        _C,
        T.OPERATIONS,
        "Where the bundled proxy's certificate comes from: Let's Encrypt (acme) or the pair "
        "uploaded in the portal (own); 'own' needs an upload (ADR 0072).",
        choices=("acme", "own"),
    ),
    S(
        "integration_check_interval_hours",
        K.INT,
        24,
        _C,
        T.OPERATIONS,
        "The worker checks every group with a Webex host or a contact-center mapping this "
        "often and reports a broken chain as a system event (ADR 0065, 0082).",
        minimum=1,
        maximum=168,
    ),
    S(
        "integration_probe_timeout_seconds",
        K.INT,
        60,
        _C,
        T.OPERATIONS,
        "How long a group check waits for the task:new and task:ended events of its probe "
        "work item before it reports the webhook path broken (ADR 0082).",
        minimum=10,
        maximum=300,
    ),
    S(
        "job_overdue_minutes",
        K.INT,
        10,
        _C,
        T.OPERATIONS,
        "A pending job this many minutes past its planned time counts as overdue in a "
        "group's integration check (ADR 0068).",
        minimum=1,
        maximum=1440,
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
        "integration_request_retention_days",
        K.INT,
        30,
        _C,
        T.RETENTION,
        "Days after which request events (every call to Webex and WxCC for a booking) leave "
        "the audit log, apart from the other system events (ADR 0068).",
        minimum=1,
        maximum=365,
    ),
    S(
        "group_erase_after_days",
        K.INT,
        30,
        _C,
        T.RETENTION,
        "Days after a group was archived before the worker erases its row, once nothing refers "
        "to it any more (ADR 0084); the audit log keeps the slug meanwhile.",
        minimum=1,
        maximum=365,
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
        "security_event_max_sources",
        K.INT,
        1000,
        _C,
        T.OPERATIONS,
        "Windows (kind of event × caller) a process keeps; beyond that, callers count as many "
        "(ADR 0053).",
        minimum=10,
        maximum=100_000,
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
        "max_font_unpacked_bytes",
        K.INT,
        2_000_000,
        _C,
        T.OPERATIONS,
        "Largest unpacked size (WOFF2 totalSfntSize) of an uploaded font, in bytes (ADR 0092).",
        minimum=256_000,
        maximum=8_000_000,
    ),
    S(
        "configuration_package_max_mb",
        K.INT,
        64,
        _C,
        T.OPERATIONS,
        "Largest configuration package (MB) the portal and the API accept for import (ADR 0089).",
        minimum=1,
        maximum=512,
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
        "rate_limit_unknown_links",
        K.RATE,
        "20/hour",
        _C,
        T.RATE_LIMITS,
        "Unknown management links per IP address.",
        minimum=5,
        maximum=100,
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
    S(
        "admin_idle_minutes",
        K.INT,
        60,
        _C,
        T.ADMIN_SESSION,
        "Idle time after which an admin session ends (ADR 0098).",
        minimum=15,
        maximum=120,
    ),
    S(
        "admin_max_sessions_per_person",
        K.INT,
        5,
        _C,
        T.ADMIN_SESSION,
        "Admin sessions one person may hold at once; the one idle longest lapses (ADR 0098).",
        minimum=1,
        maximum=20,
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
        "oidc_role_agent",
        K.STRING,
        "openplanner-agent",
        _C,
        T.LOGIN_MAPPING,
        "Role value of an agent who works a group's own queue (ADR 0043).",
    ),
    S(
        "oidc_entitlement_claim",
        K.STRING,
        "openplanner_entitlements",
        _C,
        T.LOGIN_MAPPING,
        "Claim holding the entitlements, one value per role and target (ADR 0049).",
    ),
    S(
        "oidc_entitlement_prefix",
        K.STRING,
        "openplanner",
        _C,
        T.LOGIN_MAPPING,
        "Prefix of every entitlement value; separates installations that share one IdP.",
    ),
    S(
        "oidc_claim_mode",
        K.ENUM,
        "legacy",
        _B,
        T.LOGIN,
        "Where rights come from: legacy (three claims), shadow (compare), entitlements "
        "(ADR 0049); moving back to legacy weakens a protection, so env only.",
        choices=CLAIM_MODES,
    ),
    S(
        "oidc_entitlements_max_values",
        K.INT,
        200,
        _C,
        T.LOGIN_MAPPING,
        "Values in the entitlement claim above which the login is refused (ADR 0049).",
        minimum=20,
        maximum=1000,
    ),
    S(
        "idp_mapping_refused_window_days",
        K.INT,
        30,
        _C,
        T.STAFF,
        "How far back the identity provider mapping page lists refused values (ADR 0049).",
        minimum=7,
        maximum=365,
    ),
    S(
        "idp_max_per_organisation",
        K.INT,
        2,
        _C,
        T.LOGIN_MAPPING,
        "Identity providers an organisation may register (ADR 0083); two allows moving from "
        "one to the next without a gap.",
        minimum=1,
        maximum=5,
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
        "smtp_plaintext_relay_allowed",
        K.BOOL,
        False,
        _B,
        T.MAIL,
        "Allow mail without TLS in production, for a relay on a trusted network (ADR 0091); "
        "otherwise saving it is refused and System status reports it. Never in the portal.",
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
    S(
        "update_channel",
        K.ENUM,
        "release",
        _C,
        T.UPDATES,
        "What the installation follows (ADR 0059, 0065): releases only, or release "
        "candidates (`rc`) too; `rc` is not usable in production.",
        choices=("release", "rc"),
    ),
    S(
        "update_auto_apply",
        K.BOOL,
        False,
        _C,
        T.UPDATES,
        "The worker requests a newer version itself as soon as the check finds one "
        "(ADR 0059, 0065); not usable in production.",
    ),
    S(
        "update_check_minutes",
        K.INT,
        60,
        _C,
        T.UPDATES,
        'Minutes between two release checks by the worker (ADR 0067); "Check now" on the '
        "updates page asks for one ahead of time.",
        minimum=5,
        maximum=1440,
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
        "product_footer",
        K.BOOL,
        True,
        _D,
        T.DISPLAY,
        '"Appointments via OpenPlanner" with the product mark at the foot of the booking pages '
        "and in the HTML part of emails (ADR 0060 §6).",
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
    S(
        "planner_changes_alert_per_hour",
        K.INT,
        50,
        _C,
        T.PLANNER,
        "Changes by one planner per hour above which a system event warns (ADR 0044).",
        minimum=5,
        maximum=1000,
    ),
    S(
        "queue_refresh_seconds",
        K.INT,
        10,
        _C,
        T.PLANNER,
        "Seconds between two refreshes of the own queue, a claimed booking and the participant's "
        "waiting page (ADR 0072).",
        minimum=5,
        maximum=60,
    ),
    S(
        "meeting_status_refresh_seconds",
        K.INT,
        30,
        _C,
        T.PLANNER,
        "Seconds a meeting status on the agenda may be old before the video provider is asked "
        "(webhooks come first), and between two refreshes of the agenda's status columns "
        "(ADR 0010).",
        minimum=10,
        maximum=300,
    ),
    S(
        "planner_rebook_days",
        K.INT,
        14,
        _D,
        T.PLANNER,
        "Days after the original start after which an unanswered request to rebook lapses.",
        minimum=1,
        maximum=30,
    ),
    S(
        "planner_rebook_reminder_days",
        K.INT,
        3,
        _D,
        T.PLANNER,
        "Days before the lapse at which the one reminder to rebook goes (below the lapse).",
        minimum=1,
        maximum=29,
    ),
    S(
        "booking_form_max_custom_fields",
        K.INT,
        5,
        _C,
        T.BOOKING_FORM,
        "Custom fields a group's booking form may have at most; 0 switches them off (ADR 0055).",
        minimum=0,
        maximum=15,
    ),
    S(
        "phone_default_region",
        K.ENUM,
        "NL",
        _D,
        T.BOOKING_FORM,
        "Country of phone numbers typed without a country code (ADR 0055).",
        choices=PHONE_REGIONS,
    ),
    S(
        "booking_confirm_window_hours",
        K.INT,
        24,
        _D,
        T.CONFIRMATION,
        "Hours after booking within which the participant confirms by email link (ADR 0056).",
        minimum=1,
        maximum=72,
    ),
    S(
        "booking_confirm_cutoff_hours",
        K.INT,
        2,
        _D,
        T.CONFIRMATION,
        "Hours before the start by which the participant confirms at the latest (ADR 0056).",
        minimum=0,
        maximum=24,
    ),
    S(
        "booking_confirm_reminders",
        K.INT,
        1,
        _D,
        T.CONFIRMATION,
        "Reminders to confirm, spread evenly over the time to confirm (ADR 0056).",
        minimum=0,
        maximum=3,
    ),
    S(
        "booking_confirm_min_minutes",
        K.INT,
        15,
        _C,
        T.CONFIRMATION,
        "Less time to confirm than this: confirm until the change deadline, no reminder.",
        minimum=5,
        maximum=120,
    ),
    S(
        "participant_link_session_hours",
        K.INT,
        24,
        _C,
        T.PARTICIPANT_LINK,
        "Hours after its last use that a participant's browser may still use a booking it "
        "opened from the emailed link (ADR 0093); the email link always works again.",
        minimum=20,
        maximum=168,
    ),
)
"""Every setting of the service, in the order of ``docs/settings.md``."""

BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in REGISTRY}
"""The registry by key."""


def lookup(name: str) -> SettingSpec | None:
    """Return the setting for a key or variable name (any case, aliases included)."""
    wanted = name.strip().lower()
    return BY_KEY.get(wanted) or next((s for s in REGISTRY if wanted in s.aliases), None)
