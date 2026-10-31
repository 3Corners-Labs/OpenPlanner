"""What the architecture plates say: building blocks, layers, integrations (single source).

Every output in ``docs/diagrams/`` (draw.io, PNG, PDF, DOCX) is generated from these tables, so a
change to the architecture is one edit here followed by ``make diagrams``. Labels may contain
the HTML that draw.io renders (``<b>``, ``<br>``, ``&amp;``); the document builders strip it.
"""

from dataclasses import dataclass
from enum import StrEnum

AS_OF = "27 oktober 2026"
TITLE = "Bouwblokken van OpenPlanner"
SUBTITLE = (
    "Architectuur van de groepsafsprakenservice voor het contactcenter, zoals gebouwd. "
    f"Stand {AS_OF}."
)
INTRO = (
    "Plaat 1 toont de hoofdlijn met zeventien bouwblokken (B1–B17) in vier families en de "
    "omgeving; plaat 2 de softwarecomponenten per laag; plaat 3 de integraties met hun poorten en "
    "adapters. Het register achteraan beschrijft per bouwblok de verantwoordelijkheid, de modules "
    "en de besluiten. De bewerkbare bron van de platen is openplanner-architectuur.drawio."
)
LEGEND_TEXT = (
    "Kleuren: groen = burgerkanaal, blauw = kern (domein), oranje = beheer, paars = platform, "
    "grijs = extern. Gestippeld = ontworpen, nog niet gebouwd."
)


class Family(StrEnum):
    """The colour family of a block; the same on every plate and in the register."""

    PARTICIPANT = "participant"
    CORE = "core"
    ADMIN = "admin"
    PLATFORM = "platform"
    EXTERNAL = "ext"
    NONE = "none"


FAMILY_NAMES = {
    Family.PARTICIPANT: "Burgerkanaal",
    Family.CORE: "Kern (domein)",
    Family.ADMIN: "Beheer",
    Family.PLATFORM: "Platform",
    Family.EXTERNAL: "Extern",
}


@dataclass(frozen=True)
class Plate:
    """One page of the draw.io file and one section of the documents."""

    title: str
    description: str


PLATES = (
    Plate(
        "Hoofdlijn: bouwblokken en omgeving",
        "Zeventien bouwblokken in vier families. Links wie de dienst gebruikt, rechts de systemen "
        "waar OpenPlanner mee praat. Burgers loggen nooit in; alleen beheerders via de identity "
        "provider. Persoonsgegevens staan alleen op Booking.",
    ),
    Plate(
        "Softwarecomponenten per laag",
        "De Python-pakketten onder app/, van ingang naar zuivere bedrijfsregels. Pijlen in "
        "importrichting, afgedwongen door import-linter. Elk component draagt zijn bouwblok-ID.",
    ),
    Plate(
        "Integraties en adapters",
        "Elk extern systeem zit achter een poort uit domain/ports.py en heeft één adapter in "
        "app/adapters/. Per baan: poort, adapter, verbinding, extern systeem en wat er heen en "
        "terug gaat.",
    ),
)


@dataclass(frozen=True)
class Block:
    """A building block: plate-1 box and register row."""

    id: str
    family: Family
    name: str
    summary: str
    responsibility: str
    modules: str
    adrs: str
    planned: bool = False


BLOCKS = (
    Block(
        "B1",
        Family.PARTICIPANT,
        "Burgerportaal",
        "groepspagina /g/slug, maandkalender, beheerpagina /b/token, join-pagina",
        "Groepspagina /g/{slug}, maandkalender als progressive enhancement, beheerpagina "
        "/b/{token} voor verzetten en annuleren, join-pagina. Werkt zonder JavaScript en zonder "
        "muis.",
        "api/booking_pages · participant · slot_picker · join_pages · web/static/calendar.js",
        "0020",
    ),
    Block(
        "B2",
        Family.PARTICIPANT,
        "Widget",
        "Web Component, dezelfde tokens",
        "Dezelfde boekflow als Web Component in Shadow DOM, gestyled via --booking-*-tokens en "
        "part. Gepland (WP3c).",
        "widget/",
        "0020",
        planned=True,
    ),
    Block(
        "B3",
        Family.PARTICIPANT,
        "Direct videobellen",
        "nu bellen, plafond per groep, plek in de rij",
        "'Nu bellen' per groep: meeting en work item direct, plafond op open gesprekken, plek in "
        "de rij en gebruikelijke wachttijd uit eigen data.",
        "api/direct_pages · domain/direct_queue",
        "0019 · 0026",
    ),
    Block(
        "B4",
        Family.CORE,
        "Planning &amp; beschikbaarheid",
        "bezettingsblokken, feestdagen, noodsluiting",
        "Bezettingsblokken met agentaantal, uitzonderingen per datum, feestdagkalenders per "
        "organisatie of groep, noodsluiting. Rekent vrije slots uit in de tijdzone van de groep.",
        "domain/scheduling · availability · planning · holidays · closure",
        "0024 · 0025 · 0027",
    ),
    Block(
        "B5",
        Family.CORE,
        "Boekingen &amp; tokens",
        "boeken, verzetten, annuleren",
        "Boeken, verzetten en annuleren in één transactie onder schrijfslot; capaciteit-snapshot "
        "op het slot; beheertoken afgeleid uit salt en serversleutel, alleen de hash opgeslagen.",
        "domain/booking · services · tokens · adapters/db/uow",
        "0011",
    ),
    Block(
        "B6",
        Family.CORE,
        "Berichten &amp; herinneringen",
        "mail, SMS, .ics, teksten",
        "Bevestiging, wijziging, herinneringen per groepsregel en annulering via mail en SMS. "
        "Vaste lay-outs met bewerkbare tekstblokken; .ics-bijlage zonder persoonsgegevens.",
        "domain/channels · messaging_service · message_texts · jobs/messages · calendar · "
        "adapters/smtp · sms · senders",
        "0009 · 0022 · 0028",
    ),
    Block(
        "B7",
        Family.CORE,
        "Gespreksregie",
        "meeting, work item, tijdlijn",
        "Meeting aanmaken namens de host van de groep, work item naar WxCC op T-dispatch, "
        "ondertekende webhooks terug, tijdlijn en gespreksstatus per boeking.",
        "domain/calls · call_service · integration · jobs/calls · adapters/webex_meetings · "
        "wxcc · webhooks",
        "0017 · 0018 · 0021",
    ),
    Block(
        "B8",
        Family.CORE,
        "Privacy &amp; retentie",
        "purge_pii op einde + 24 u; noodsluiting max. 30 d",
        "purge_pii op slot-einde + 24 u of annulering + 24 u; bij noodsluiting uiterlijk 30 "
        "dagen na het einde. Nachtelijke sweep en alarm volgen in WP8.",
        "domain/job_plan · jobs/runner · logging",
        "0024",
    ),
    Block(
        "B9",
        Family.ADMIN,
        "Beheerportaal",
        "groepen, teksten, kanalen, agenda",
        "Groepen, bezetting, teksten, kanalen, branding, agenda en tijdlijn. Server-rendered "
        "formulieren zonder JavaScript.",
        "admin/*_routes · forms · pages · templates/admin",
        "0016",
    ),
    Block(
        "B10",
        Family.ADMIN,
        "Autorisatie &amp; audit",
        "één matrix, scopes, audit_events",
        "Eén matrix rol × permissie × niveau; elke route één permissie; queries gefilterd op "
        "Scope; weigeringen, logins en wijzigingen in audit_events; docs/authorization.md "
        "gegenereerd.",
        "domain/authorization · audit · admin/authz · web/authz_report",
        "0014 · 0015",
    ),
    Block(
        "B11",
        Family.ADMIN,
        "Machine-API",
        "/api/agent/v1, client credentials",
        "/api/agent/v1 voor contactcenter-bots: client credentials, opake tokens van 10 minuten, "
        "scopes per client, standaard uit, geen persoonsgegevens.",
        "api/agent/ · domain/api_access · api_clients",
        "0023",
    ),
    Block(
        "B17",
        Family.ADMIN,
        "Inlog (OIDC)",
        "PKCE, nonce, verse login",
        "Beheerderslogin via PingFederate (Keycloak in dev) met PKCE, state en nonce; sessie van "
        "8 uur; wijzigingen vragen een login van hooguit 60 minuten.",
        "adapters/oidc · admin/session",
        "0013 · 0015",
    ),
    Block(
        "B12",
        Family.PLATFORM,
        "Taakplanner",
        "jobs-tabel, worker",
        "Eigen jobs-tabel, gevuld in de boekingstransactie; worker claimt onder schrijfslot, retry "
        "na 1, 5, 15 min; pinned_start maakt jobs idempotent.",
        "adapters/db/jobs · jobs/worker · runner · healthcheck",
        "0002",
    ),
    Block(
        "B13",
        Family.PLATFORM,
        "Opslag",
        "SQLite WAL, Alembic, versleutelde secrets",
        "SQLite in WAL-modus op een gedeeld volume, Alembic-migraties (alleen web migreert), "
        "integratiesecrets AES-256-GCM versleuteld en alleen-schrijven.",
        "adapters/db/ · crypto · db/credentials · migrations/",
        "0008",
    ),
    Block(
        "B14",
        Family.PLATFORM,
        "Taal &amp; huisstijl",
        "i18n nl/en, branding",
        "Nederlands standaard, Engels verplicht; taal via ?lang=, cookie, browser, organisatie; "
        "branding per organisatie via tokens.",
        "i18n/ · web/locale · web/branding · domain/branding",
        "0004 · 0020",
    ),
    Block(
        "B15",
        Family.PLATFORM,
        "Rand &amp; beveiliging",
        "TLS, headers, CSRF, rate limits",
        "Caddy met automatisch vernieuwde certificaten, strikte CSP en HSTS, CSRF via Fetch "
        "Metadata, rate limits en honeypot.",
        "deploy/proxy · web/protection · api/security_headers",
        "0030",
    ),
    Block(
        "B16",
        Family.PLATFORM,
        "Updates &amp; levering",
        "release-check, host-updater, cosign",
        "Ondertekende images (cosign) met SBOM; worker kijkt elk uur naar releases; superuser "
        "vraagt update aan; host-updater verifieert, maakt backup en rolt terug bij een ongezonde "
        "start.",
        "domain/updates · update_service · adapters/releases · scripts/updater/",
        "0006 · 0029",
    ),
)

# Plate 1 shows one extra platform box that is not a numbered building block.
LOGGING_BOX = ("Logging &amp; health", "structlog JSON, PII-masker, /healthz")

ACTORS = (
    ("Burger", "boekt, wijzigt, belt in", True),
    ("Website organisatie", "beslisboom, linkt naar /g/slug", True),
    ("Beheerder", "groep, organisatie, superuser", True),
    ("Contactcenter-bot", "WxCC AI Agent, vraagt vrije tijden", True),
    ("Agent", "krijgt work item via WxCC, host in Webex", False),
)

EXTERNALS = (
    ("Mailprovider", "SMTP per organisatie · B6"),
    ("SMS-gateway", "vandaag Webex Connect · B6"),
    ("Webex Meetings", "meeting + webhooks · B7"),
    ("Webex Contact Center", "work item, taakevents · B7"),
    ("Identity provider", "PingFederate / Keycloak · B17"),
    ("Let's Encrypt", "ACME-certificaten · B15"),
    ("GitHub / GHCR", "releases, ondertekende images · B16"),
)

PLATE1_FOOTNOTE = (
    "Burgers loggen nooit in; alleen beheerders via de IdP. Persoonsgegevens staan alleen op "
    "<b>Booking</b>; naar WxCC gaan alleen voornaam en host-link."
)


@dataclass(frozen=True)
class Component:
    """A box in a layer of plate 2; ports have no family."""

    family: Family | None
    tags: str
    name: str
    modules: str = ""


@dataclass(frozen=True)
class Layer:
    """A horizontal band of plate 2, with an optional note to its right."""

    label: str
    package: str
    components: tuple[Component, ...]
    per_row: int
    box_height: int
    note: str = ""
    arrow_in: str = ""
    is_ports: bool = False


C = Component
LAYERS = (
    Layer(
        "Ingangen",
        "api · admin · jobs · cli",
        (
            C(
                Family.PARTICIPANT,
                "B1",
                "Burgerpagina's",
                "api/booking_pages · participant · slot_picker",
            ),
            C(Family.PARTICIPANT, "B1 B7", "Join-pagina", "api/join_pages"),
            C(Family.PARTICIPANT, "B3", "Direct bellen", "api/direct_pages"),
            C(Family.CORE, "B7", "Webhooks in", "api/webhooks"),
            C(Family.ADMIN, "B11", "Machine-API", "api/agent/ (token, RequireScope)"),
            C(Family.ADMIN, "B9 B10", "Beheerportaal", "admin/*_routes · authz · session"),
            C(Family.PLATFORM, "B12 B6 B8", "Worker", "jobs/worker · runner · messages · calls"),
            C(Family.PLATFORM, "B16", "Operator-CLI", "cli (authz-matrix, wxcc-check, update)"),
            C(Family.PLATFORM, "", "Health", "api/health · jobs/healthcheck"),
        ),
        5,
        62,
        note="<b>Regel</b><br>Ingangen importeren elkaar nooit.",
    ),
    Layer(
        "Web",
        "app.web",
        (
            C(Family.PLATFORM, "B14", "render()", "templates · locale · branding"),
            C(Family.PLATFORM, "B15", "Bescherming", "protection (CSRF, rate limit)"),
            C(Family.PLATFORM, "", "Foutpagina's", "error_pages · errors"),
            C(Family.ADMIN, "B10", "Labels &amp; rapport", "authz_labels · authz_report"),
            C(Family.CORE, "B7 B4", "Weergavelabels", "call_labels · closure_labels · slot_values"),
        ),
        5,
        62,
        arrow_in="importeert",
    ),
    Layer(
        "Adapters",
        "app.adapters",
        (
            C(Family.PLATFORM, "B13", "db", "uow · engine · models · scope-filter"),
            C(Family.PLATFORM, "B12", "Jobqueue", "db/jobs"),
            C(Family.PLATFORM, "B13", "Secrets", "crypto · db/credentials (AES-GCM)"),
            C(Family.CORE, "B6", "Mail", "smtp"),
            C(Family.CORE, "B6", "SMS", "senders · sms · webex_connect_sms"),
            C(Family.CORE, "B7", "Webex Meetings", "webex_meetings · webex_auth"),
            C(Family.CORE, "B7", "WxCC", "wxcc · wxcc_config · webhooks"),
            C(Family.ADMIN, "B17", "OIDC", "oidc (httpx + PyJWT)"),
            C(Family.PLATFORM, "B16", "Releases", "releases · db/backup · db/updates"),
            C(Family.PLATFORM, "", "Klok", "clock"),
        ),
        5,
        62,
        arrow_in="importeert",
    ),
    Layer(
        "Poorten",
        "domain/ports.py",
        tuple(
            C(None, "", name)
            for name in (
                "Transaction · UnitOfWork",
                "MessageSender · SenderDirectory",
                "VideoProvider",
                "ContactCenterDispatcher",
                "IdentityProvider",
                "ReleaseSource",
                "Clock",
            )
        ),
        4,
        36,
        note="<b>Tests</b><br>Elke poort heeft een fake in tests/fakes/.",
        arrow_in="implementeert",
        is_ports=True,
    ),
    Layer(
        "Domeinservices",
        "app.domain",
        tuple(
            C(fam, tag, name)
            for fam, tag, name in (
                (Family.CORE, "B5", "BookingService"),
                (Family.CORE, "B4", "PlanningService"),
                (Family.CORE, "B4", "HolidayService"),
                (Family.CORE, "B4", "ClosureService"),
                (Family.CORE, "B6", "MessagingService"),
                (Family.CORE, "B6", "ChannelSettingsService"),
                (Family.CORE, "B7", "CallService"),
                (Family.CORE, "B7", "IntegrationService"),
                (Family.ADMIN, "B9 B10", "AdminService"),
                (Family.ADMIN, "B9", "OrganisationService"),
                (Family.ADMIN, "B11", "ApiAccessService"),
                (Family.PLATFORM, "B14", "BrandingService"),
                (Family.PLATFORM, "B16", "UpdateService"),
            )
        ),
        5,
        44,
        note="<b>Regel</b><br>Geen FastAPI, SQLAlchemy of httpx in app.domain. Fouten dragen "
        "codes, geen tekst.",
        arrow_in="gebruikt",
    ),
    Layer(
        "Domeinregels",
        "app.domain",
        (
            C(Family.ADMIN, "B10", "authorization", "rol × permissie × niveau"),
            C(Family.CORE, "B4", "availability · scheduling", "slots, tijdzones, zomertijd"),
            C(Family.CORE, "B4", "holidays · closure"),
            C(Family.CORE, "B5", "booking · tokens", "HMAC-salt, sha256(token)"),
            C(Family.CORE, "B8 B12", "job_plan", "pinned_start, purge_pii"),
            C(Family.CORE, "B6", "channels · message_texts"),
            C(Family.CORE, "B7 B3", "calls · direct_queue"),
            C(Family.ADMIN, "B10", "audit"),
            C(Family.PLATFORM, "B16", "updates"),
            C(Family.NONE, "", "errors", "foutcodes"),
        ),
        5,
        56,
    ),
)

I18N_NOTE = "negotiation · translator · po · dates (Babel); importeert niets uit de rest van app/"


@dataclass(frozen=True)
class Lane:
    """One integration on plate 3: port, adapter, wire, external system and the data."""

    ports: tuple[str, ...]
    family: Family
    tags: str
    adapter: str
    modules: str
    wire: str
    system: str
    system_note: str
    outbound: str
    inbound: str
    port_note: str = ""
    incoming: bool = False


LANES = (
    Lane(
        ("MessageSender", "SenderDirectory"),
        Family.CORE,
        "B6",
        "Mail",
        "smtp · senders",
        "SMTP, STARTTLS / TLS<br>login uit kanaalinstelling",
        "Mailprovider",
        "per organisatie; dev: Mailpit",
        "mail aan burger: voornaam, datum, team, beheerlink, .ics",
        "alleen foutcode (unreachable, tls_failed, auth_failed …)",
    ),
    Lane(
        ("MessageSender",),
        Family.CORE,
        "B6",
        "SMS",
        "sms · webex_connect_sms · senders (SMS_GATEWAYS)",
        "HTTPS POST /v1/sms/messages<br>API-key header",
        "SMS-gateway",
        "vandaag Webex Connect; andere provider = extra adapter",
        "telefoonnummer + tekst (GSM-7 gecontroleerd)",
        "201; antwoordbody wordt nooit gelogd",
    ),
    Lane(
        ("VideoProvider",),
        Family.CORE,
        "B7",
        "Webex Meetings",
        "webex_meetings · webex_auth",
        "HTTPS REST /v1/meetings<br>Service App, OAuth refresh-token",
        "Webex Meetings",
        "namens de host van de groep",
        "groepsnaam en tijden; verwijderen bij annuleren",
        "meeting-id, join-link · webhooks gestart, host/gast binnen, beëindigd "
        "(HMAC-SHA1, replay-sleutel)",
    ),
    Lane(
        ("ContactCenterDispatcher",),
        Family.CORE,
        "B7",
        "WxCC",
        "wxcc · webex_auth",
        "Create Task v2 · Task Messages v2 · End Task v1<br>Service App, scopes cjp:task_*",
        "Webex Contact Center",
        "flow routeert op openplannerGroup",
        "work item: referentie, voornaam, start, groep, meetinglink, soort",
        "task-id · taakevents via subscription (HMAC-SHA256, ±300 s)",
    ),
    Lane(
        ("IdentityProvider",),
        Family.ADMIN,
        "B17",
        "OIDC",
        "oidc (httpx + PyJWT)",
        "authorization code + PKCE S256<br>client_secret_basic, JWKS",
        "Identity provider",
        "PingFederate; dev: Keycloak",
        "redirect met state, nonce, code_challenge",
        "ID-token → claims → Principal via ClaimMapping",
    ),
    Lane(
        ("ReleaseSource",),
        Family.PLATFORM,
        "B16",
        "Releases",
        "releases",
        "HTTPS GET /releases/latest<br>optioneel read-only token",
        "GitHub / GHCR",
        "releases en ondertekende images",
        "elk uur vanuit de worker",
        "versietag en releasepagina; pull + cosign-controle door de host-updater",
    ),
    Lane(
        ("Transaction", "UnitOfWork"),
        Family.PLATFORM,
        "B13 B12",
        "Database",
        "db/uow · engine · models · jobs",
        "aiosqlite, WAL<br>BEGIN IMMEDIATE voor schrijven",
        "SQLite-volume",
        "gedeeld door web en worker",
        "queries gefilterd op Scope",
        "domeinobjecten; PII alleen in bookings",
    ),
    Lane(
        (),
        Family.ADMIN,
        "B11",
        "Machine-API",
        "api/agent (token, RequireScope)",
        "OAuth client credentials<br>opake token 10 min, IP-allowlist",
        "Contactcenter-bot",
        "WxCC AI Agent",
        "JSON zonder persoonsgegevens",
        "vraagt vrije tijden per groep",
        port_note="geen poort (ingang)",
        incoming=True,
    ),
    Lane(
        (),
        Family.PLATFORM,
        "B15",
        "Proxy",
        "deploy/proxy (Caddy)",
        "ACME HTTP-01<br>eigen proxy.env, geen app-secrets",
        "Let's Encrypt",
        "certificaat voor TLS_DOMAIN",
        "aanvraag en vernieuwing",
        "certificaat in volume proxy-data",
        port_note="geen poort (rand)",
    ),
)


@dataclass(frozen=True)
class SharedPart:
    """A module that serves several integrations (bottom of plate 3)."""

    family: Family
    name: str
    modules: str
    note: str


SHARED_PARTS = (
    SharedPart(
        Family.PLATFORM,
        "Samenstellen",
        "integrations.py",
        "bouwt alle adapters uit de settings voor web en worker; zonder Service App draait "
        "OpenPlanner zonder video en work items",
    ),
    SharedPart(
        Family.CORE,
        "Webex-tokens",
        "webex_auth.py",
        "refresh 5 min voor verloop of na 401; geroteerde tokens versleuteld in de database",
    ),
    SharedPart(
        Family.CORE,
        "Webhooks in",
        "webhooks.py · api/webhooks",
        "handtekening over de ruwe body vóór het parsen; replay-sleutel per Webex-event",
    ),
    SharedPart(
        Family.PLATFORM,
        "Secrets",
        "crypto.py · db/credentials",
        "AES-256-GCM met HKDF-sleutel, recordnaam als associated data, alleen-schrijven in het "
        "portaal",
    ),
    SharedPart(
        Family.CORE,
        "Registratie",
        "webhook_registration.py",
        "register-webhooks: idempotent, Webex-webhooks per host-account, één WxCC-subscription",
    ),
    SharedPart(
        Family.CORE,
        "Diagnose",
        "diagnostics.py · wxcc_config.py",
        "wxcc-check probeert elke call onschadelijk; wxcc-config-check leest de keten asset → "
        "flow → queue",
    ),
)

PLATE3_FOOTNOTE = (
    "Fouten van buiten worden korte codes (IntegrationError); antwoorden van providers komen nooit "
    "in logs. Elke poort heeft een fake in tests/fakes/."
)
