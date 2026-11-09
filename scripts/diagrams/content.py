"""What the architecture plates say: building blocks, layers, integrations (single source).

Every output in ``docs/diagrams/`` (draw.io, PNG, PDF, DOCX) is generated from these tables, so a
change to the architecture is one edit here followed by ``make diagrams``. Labels may contain
the HTML that draw.io renders (``<b>``, ``<br>``, ``&amp;``); the document builders strip it.
"""

from dataclasses import dataclass
from enum import StrEnum

AS_OF = "5 november 2026"
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
    "Kleuren: groen = deelnemerskanaal, blauw = kern (domein), oranje = beheer, paars = platform, "
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
    Family.PARTICIPANT: "Deelnemerskanaal",
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
        "waar OpenPlanner mee praat. Deelnemers loggen nooit in; alleen beheerders via de identity "
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
        "Deelnemersportaal",
        "groepspagina /g/slug, maandkalender, beheerpagina, proefgesprek, join-pagina",
        "Groepspagina /g/{slug} met de formuliervelden van de groep, maandkalender als progressive "
        "enhancement, beheerpagina /b/m/{ref} voor verzetten en annuleren (de link uit de mail "
        "wordt ingewisseld voor een cookie), proefgesprek en join-pagina. Werkt zonder JavaScript "
        "en zonder muis.",
        "api/booking_pages · participant · slot_picker · link_session · trial_pages · join_pages · "
        "web/static/calendar.js",
        "0020 · 0069 · 0071 · 0093",
    ),
    Block(
        "B2",
        Family.PARTICIPANT,
        "Widget",
        "Web Component, dezelfde tokens",
        "Dezelfde boekflow als Web Component <openplanner-booking> in Shadow DOM, gestyled via "
        "--booking-*-tokens en part; werkt alleen op de websites die de organisatie toestaat.",
        "widget/ · api/widget_api · widget_script · domain/embedding_service",
        "0020 · 0035",
    ),
    Block(
        "B3",
        Family.PARTICIPANT,
        "Direct videobellen",
        "nu bellen per blok, plafond, plek in de rij",
        "'Nu bellen' per bezettingsblok: meeting en work item direct, plafond op open gesprekken, "
        "plek in de rij en gebruikelijke wachttijd uit eigen data, live status in de wachtruimte.",
        "api/direct_pages · domain/direct_queue · direct_leave · web/static/live-region.js",
        "0019 · 0026 · 0072",
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
        "boeken, bevestigen, verzetten, annuleren",
        "Boeken, verzetten en annuleren in één transactie onder schrijfslot; capaciteit-snapshot "
        "op het slot; eigen formuliervelden per groep; een bevestiging per mail houdt de plek "
        "vast; maximum per e-mailadres; beheertoken afgeleid uit salt en serversleutel, alleen de "
        "hash opgeslagen.",
        "domain/booking · services · tokens · form_fields · adapters/db/uow",
        "0011 · 0055 · 0056 · 0066",
    ),
    Block(
        "B6",
        Family.CORE,
        "Berichten &amp; herinneringen",
        "mail, SMS, .ics, teksten",
        "Bevestiging, wijziging, herinneringen per groepsregel en annulering via mail en SMS. "
        "Vaste lay-outs met bewerkbare tekstblokken per organisatie en groep; .ics-bijlage zonder "
        "persoonsgegevens; controle van het afzenderdomein (SPF, DKIM, DMARC).",
        "domain/channels · messaging_service · message_texts · jobs/messages · calendar · "
        "adapters/smtp · sms · senders · mail_dns",
        "0009 · 0022 · 0028 · 0073 · 0074",
    ),
    Block(
        "B7",
        Family.CORE,
        "Gespreksregie",
        "meeting, work item, tijdlijn, controle",
        "Meeting aanmaken namens de host van de groep, work item naar WxCC op T-dispatch, "
        "ondertekende webhooks terug, tijdlijn en gespreksstatus per boeking met de reden van de "
        "provider; groepscontrole met een proef-work item.",
        "domain/calls · call_service · integration · jobs/calls · adapters/webex_meetings · "
        "wxcc · webhooks · group_check",
        "0017 · 0018 · 0021 · 0065 · 0068 · 0082",
    ),
    Block(
        "B8",
        Family.CORE,
        "Privacy &amp; retentie",
        "purge_pii op einde + 24 u; noodsluiting max. 30 d; nachtelijke sweep",
        "purge_pii op slot-einde + 24 u of annulering + 24 u. Bewust later bij een noodsluiting "
        "(30 dagen na het einde, een open sluiting begrensd) en bij wachten op een nieuw tijdstip "
        "of op een bevestiging per mail. Een nachtelijke sweep verwijdert achterblijvers en geeft "
        "alarm.",
        "logging · domain/privacy · retention · retention_sweep · job_plan · jobs/runner · "
        "housekeeping",
        "0024 · 0044 · 0056 · 0102",
    ),
    Block(
        "B9",
        Family.ADMIN,
        "Beheerportaal",
        "groepen, teksten, kanalen, agenda, instellingen",
        "Groepen, bezetting, teksten, kanalen, branding, agenda, tijdlijn en instellingen, "
        "geordend als scope → object → tab vanuit één navigatieregister, met de handleiding in het "
        "portaal. Server-rendered formulieren zonder JavaScript.",
        "admin/*_routes · forms · pages · web/navigation · templates/admin",
        "0016 · 0033 · 0042 · 0047",
    ),
    Block(
        "B10",
        Family.ADMIN,
        "Autorisatie &amp; audit",
        "één matrix, scopes, audit_events",
        "Eén matrix rol × permissie × niveau; elke route één permissie; queries gefilterd op "
        "Scope; weigeringen, logins en wijzigingen in audit_events en als regel naar het "
        "logplatform; docs/authorization.md gegenereerd.",
        "domain/authorization · audit · admin/authz · web/authz_report · adapters/event_sink",
        "0014 · 0015 · 0031 · 0034",
    ),
    Block(
        "B11",
        Family.ADMIN,
        "Machine-API",
        "/api/agent/v1, client credentials",
        "/api/agent/v1 voor contactcenter-bots: client credentials, opake tokens van 10 minuten, "
        "scopes per client, IP-allowlist, standaard uit, geen persoonsgegevens; ook export en "
        "import van het configuratiepakket.",
        "api/agent/ · domain/api_access · api_clients",
        "0023 · 0089",
    ),
    Block(
        "B17",
        Family.ADMIN,
        "Inlog (OIDC)",
        "IdP per organisatie, PKCE, verse login",
        "Beheerderslogin via OIDC met PKCE, state en nonce, bij de identity provider van de "
        "organisatie (gekozen op e-maildomein) of de meegeleverde Keycloak, die ook het personeel "
        "in het portaal laat beheren; tweede factor via de provider (instelbaar); sessies op de "
        "server, 8 uur; wijzigingen vragen een login van hooguit 60 minuten.",
        "adapters/oidc · discovery · idp_registry · keycloak_directory · admin/session · "
        "domain/identity_providers",
        "0013 · 0015 · 0083 · 0095 · 0098 · 0100 · 0106",
    ),
    Block(
        "B12",
        Family.PLATFORM,
        "Taakplanner",
        "jobs-tabel, worker",
        "Eigen jobs-tabel, gevuld in de boekingstransactie; worker claimt onder schrijfslot, retry "
        "na 1, 5, 15 min; pinned_start maakt jobs idempotent; dagelijks onderhoud in de worker.",
        "adapters/db/jobs · jobs/worker · runner · housekeeping · healthcheck",
        "0002",
    ),
    Block(
        "B13",
        Family.PLATFORM,
        "Opslag",
        "SQLite WAL, Alembic, versleutelde secrets",
        "SQLite in WAL-modus op een gedeeld volume, Alembic-migraties (alleen web migreert), "
        "integratiesecrets AES-256-GCM versleuteld en alleen-schrijven, met sleutel-id en rotatie "
        "zonder verlies; configuratiepakket voor export en import van alle instellingen.",
        "adapters/db/ · crypto · db/credentials · db/sealed · db/configuration_package · "
        "migrations/",
        "0008 · 0089 · 0094",
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
        "TLS-proxy, certificaten, headers, rate limits",
        "Caddy als TLS-proxy; certificaatbron in het portaal (ACME, eigen certificaat via CSR, "
        "interne CA voor labs); certificaat en publiek adres gecontroleerd op Systeemstatus; "
        "strikte CSP en HSTS, CSRF via Fetch Metadata, rate limits en honeypot.",
        "deploy/proxy · web/protection · api/security_headers · adapters/tls_probe · "
        "instance_probe · certificate_volume",
        "0030 · 0064 · 0070 · 0086 · 0112",
    ),
    Block(
        "B16",
        Family.PLATFORM,
        "Updates &amp; levering",
        "release-check, host-updater, cosign",
        "Ondertekende images (cosign) met SBOM; de worker kijkt naar releases (standaard elk uur); "
        "de superuser kiest kanaal en automatisch bijwerken en vraagt een update aan; de "
        "host-updater verifieert, maakt een backup en rolt terug bij een ongezonde start.",
        "domain/updates · update_service · adapters/releases · scripts/updater/",
        "0006 · 0029 · 0067",
    ),
)

# Plate 1: what runs where, and one extra platform box that is not a numbered building block.
SYSTEM_NOTE = "één VM · containers proxy · web · worker, optioneel idp · SQLite-volume"
LOGGING_BOX = ("Logging &amp; health", "structlog JSON, PII-masker, logbestemmingen, /healthz")

ACTORS = (
    ("Deelnemer", "boekt, wijzigt, belt in", True),
    ("Website organisatie", "beslisboom, linkt naar /g/slug", True),
    ("Beheerder", "groep, organisatie, superuser", True),
    ("Contactcenter-bot", "WxCC AI Agent, vraagt vrije tijden", True),
    ("Agent", "krijgt work item via WxCC, host in Webex", False),
)

EXTERNALS = (
    ("Mailprovider", "SMTP per organisatie · B6"),
    ("SMS-gateway", "vandaag Webex Connect · B6"),
    ("DNS", "TXT-controle afzenderdomein · B6"),
    ("Webex Meetings", "meeting + webhooks · B7"),
    ("Webex Contact Center", "work item, taakevents · B7"),
    ("Identity provider", "per organisatie of meegeleverde Keycloak · B17"),
    ("Logplatform", "syslog, Splunk, Elastic of HTTP"),
    ("Let's Encrypt", "ACME-certificaten · B15"),
    ("GitHub / GHCR", "releases, ondertekende images · B16"),
)

PLATE1_FOOTNOTE = (
    "Deelnemers loggen nooit in; alleen beheerders via de IdP. Persoonsgegevens staan alleen op "
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
                "Deelnemerspagina's",
                "api/booking_pages · participant · slot_picker",
            ),
            C(Family.PARTICIPANT, "B1 B7", "Join-pagina", "api/join_pages · trial_pages"),
            C(Family.PARTICIPANT, "B2", "Widget-API", "api/widget_api · widget_script"),
            C(Family.PARTICIPANT, "B3", "Direct bellen", "api/direct_pages"),
            C(Family.CORE, "B7", "Webhooks in", "api/webhooks"),
            C(Family.ADMIN, "B11", "Machine-API", "api/agent/ (token, RequireScope)"),
            C(Family.ADMIN, "B9 B10", "Beheerportaal", "admin/*_routes · authz · session"),
            C(
                Family.PLATFORM,
                "B12 B6 B8",
                "Worker",
                "jobs/worker · runner · housekeeping · messages · calls",
            ),
            C(
                Family.PLATFORM,
                "B16",
                "Operator-CLI",
                "cli (closure, settings, backup-db, wxcc-check …)",
            ),
            C(Family.PLATFORM, "", "Health", "api/health · instance · jobs/healthcheck"),
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
            C(
                Family.ADMIN,
                "B9 B10",
                "Navigatie &amp; labels",
                "navigation · authz_labels · authz_report",
            ),
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
            C(Family.PLATFORM, "B13", "Secrets", "crypto · db/credentials · db/sealed (AES-GCM)"),
            C(Family.CORE, "B6", "Mail", "smtp"),
            C(Family.CORE, "B6", "SMS", "senders · sms · webex_connect_sms"),
            C(Family.CORE, "B6", "DNS", "mail_dns (dnspython)"),
            C(Family.CORE, "B7", "Webex Meetings", "webex_meetings · webex_auth"),
            C(Family.CORE, "B7", "WxCC", "wxcc · wxcc_config · webhooks · contact_centers"),
            C(Family.CORE, "B7", "Controle", "group_check · service_app_check · diagnostics"),
            C(Family.ADMIN, "B17", "OIDC", "oidc · discovery · idp_registry · signing_keys"),
            C(Family.ADMIN, "B17", "Personeel", "keycloak_directory (admin-API)"),
            C(
                Family.PLATFORM,
                "B15",
                "TLS &amp; adres",
                "tls_probe · instance_probe · certificate_volume",
            ),
            C(Family.PLATFORM, "", "Logbestemmingen", "event_sink · log_shipping · log_senders"),
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
                "UsageStore · UsageUnitOfWork",
                "DigestStore · DigestUnitOfWork · StaffAddresses",
                "ConfigurationTransfer",
                "MessageSender · SenderDirectory",
                "AlertSender · AlertSenders · BotTokenStore",
                "MailDomainResolver",
                "VideoProvider",
                "ContactCenterDispatcher",
                "ContactCenterConnections · OrganisationDispatchers",
                "ConnectionRegistrar · OrganisationConnections",
                "IntegrationChecker · ProbeRegistry",
                "RequestLog",
                "IdentityProvider · StaffDirectory",
                "DiscoveryFetcher · SigningKeys",
                "CertificateProbe · InstanceProbe",
                "PemReader · CertificateVolume",
                "EventSink · LogDestinationSender",
                "ReleaseSource",
                "Clock",
            )
        ),
        5,
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
                (Family.PARTICIPANT, "B2", "EmbeddingService"),
                (Family.PARTICIPANT, "B1", "TrialCallService"),
                (Family.CORE, "B5", "BookingService"),
                (Family.CORE, "B5", "FormService"),
                (Family.CORE, "B4", "PlanningService"),
                (Family.CORE, "B4", "HolidayService"),
                (Family.CORE, "B4", "ClosureService"),
                (Family.CORE, "B6", "MessagingService"),
                (Family.CORE, "B6", "ChannelSettingsService"),
                (Family.CORE, "B7", "CallService"),
                (Family.CORE, "B7", "IntegrationService"),
                (Family.ADMIN, "B9 B10", "AdminService"),
                (Family.ADMIN, "B9", "OrganisationService"),
                (Family.ADMIN, "B9", "SettingsService"),
                (Family.ADMIN, "B11", "ApiAccessService"),
                (Family.ADMIN, "B17", "IdentityProviderService"),
                (Family.PLATFORM, "B14", "BrandingService"),
                (Family.PLATFORM, "B15", "CertificateSourceService"),
                (Family.PLATFORM, "", "LogDestinationService"),
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
            C(Family.ADMIN, "B10", "authorization · audit", "rol × permissie × niveau"),
            C(Family.CORE, "B4", "availability · scheduling", "slots, tijdzones, zomertijd"),
            C(Family.CORE, "B4", "holidays · closure"),
            C(Family.CORE, "B5", "booking · tokens", "HMAC-salt, sha256(token)"),
            C(Family.CORE, "B12", "job_plan", "pinned_start, purge_pii"),
            C(Family.CORE, "B8", "privacy · retention", "PII-velden, bewaartermijnen"),
            C(Family.CORE, "B6", "channels · message_texts"),
            C(Family.CORE, "B7 B3", "calls · direct_queue"),
            C(Family.PLATFORM, "B16", "updates"),
            C(Family.NONE, "", "errors", "foutcodes"),
        ),
        5,
        56,
    ),
)

I18N_NOTE = "negotiation · translator · po · dates (Babel); importeert niets uit de rest van app/"
OUTSIDE_NOTE = (
    "SMTP · SMS-gateway · DNS · Webex Meetings · WxCC · IdP · logplatform · GitHub · publiek "
    "adres (zie plaat 3)"
)


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
        "mail aan deelnemer: voornaam, datum, team, beheerlink, .ics",
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
        ("MailDomainResolver",),
        Family.CORE,
        "B6",
        "DNS",
        "mail_dns (dnspython)",
        "DNS TXT-query<br>resolver van de host, 5 s",
        "DNS",
        "afzenderdomein van het kanaal",
        "TXT-vragen voor SPF, DKIM-selector en DMARC van het afzenderdomein",
        "TXT-records, of geen antwoord; uitkomst op Kanalen",
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
        "groepsnaam en tijden, ook proefgesprekken; verwijderen bij annuleren",
        "meeting-id, join-link · webhooks gestart, host/gast binnen, beëindigd "
        "(HMAC-SHA1, replay-sleutel)",
    ),
    Lane(
        ("ContactCenterDispatcher", "IntegrationChecker"),
        Family.CORE,
        "B7",
        "WxCC",
        "wxcc · contact_centers · webex_auth · group_check",
        "Create Task v2 · Task Messages v2 · End/Get Task v1<br>Service App, scopes cjp:*",
        "Webex Contact Center",
        "flow routeert op openplannerGroup",
        "work item: referentie, voornaam, start, groep, meetinglink, soort; proef-work item bij de "
        "groepscontrole",
        "task-id, taakstatus · taakevents via subscription (HMAC-SHA256, ±300 s)",
    ),
    Lane(
        ("IdentityProvider", "DiscoveryFetcher", "SigningKeys", "StaffDirectory"),
        Family.ADMIN,
        "B17",
        "OIDC",
        "oidc · discovery · signing_keys · keycloak_directory (httpx + PyJWT)",
        "authorization code + PKCE S256<br>client_secret_basic of private_key_jwt, JWKS",
        "Identity provider",
        "per organisatie (e-maildomein); meegeleverde Keycloak",
        "redirect met state, nonce, code_challenge; personeel via de admin-API van de "
        "meegeleverde Keycloak",
        "ID-token → claims → Principal via ClaimMapping",
    ),
    Lane(
        ("EventSink", "LogDestinationSender"),
        Family.PLATFORM,
        "",
        "Logbestemmingen",
        "event_sink · log_shipping · log_senders",
        "syslog (RFC 5424, TLS) · HTTPS POST<br>Splunk HEC, Elastic _bulk, NDJSON",
        "Logplatform",
        "per bestemming ingesteld door de superuser",
        "gemaskeerde JSON-regels; audit-, systeem- en beveiligingsevents ook op stdout",
        "alleen status en reden, nooit een antwoordbody",
    ),
    Lane(
        ("ReleaseSource",),
        Family.PLATFORM,
        "B16",
        "Releases",
        "releases",
        "HTTPS GET /releases<br>optioneel read-only token",
        "GitHub / GHCR",
        "releases en ondertekende images",
        "vanuit de worker, standaard elk uur",
        "versies met releasenotes; pull + cosign-controle door de host-updater",
    ),
    Lane(
        (
            "Transaction",
            "UnitOfWork",
            "UsageStore",
            "UsageUnitOfWork",
            "DigestStore",
            "DigestUnitOfWork",
            "StaffAddresses",
            "ConfigurationTransfer",
        ),
        Family.PLATFORM,
        "B13 B12",
        "Database",
        "db/uow · engine · models · jobs · configuration_package",
        "aiosqlite, WAL<br>BEGIN IMMEDIATE voor schrijven",
        "SQLite-volume",
        "gedeeld door web en worker",
        "queries gefilterd op Scope; configuratiepakket onder een wachtwoordzin",
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
        "vraagt vrije tijden per groep; configuratiepakket met een eigen scope",
        port_note="geen poort (ingang)",
        incoming=True,
    ),
    Lane(
        ("CertificateVolume", "PemReader"),
        Family.PLATFORM,
        "B15",
        "Certificaten",
        "certificate_volume · certificate_pem · internal_ca · deploy/proxy (Caddy)",
        "ACME HTTP-01 door Caddy<br>eigen proxy.env, geen app-secrets",
        "Let's Encrypt",
        "of eigen certificaat (CSR), interne CA voor labs",
        "aanvraag en vernieuwing",
        "ACME-certificaat in volume proxy-data; eigen sleutel en keten in portal-certs, de "
        "sleutel nooit in de database",
    ),
    Lane(
        ("CertificateProbe", "InstanceProbe"),
        Family.PLATFORM,
        "B15",
        "Probes",
        "tls_probe · instance_probe",
        "TLS-handshake · HTTPS GET /api/v1/instance<br>CA-bundel van de host, geen redirect",
        "Publiek adres",
        "OpenPlanner zelf, via DNS en proxy",
        "client hello; verzoek met nonce, zoals een browser",
        "certificaat (uitgever, verloop) en instance-id; uitkomst op Systeemstatus",
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
        "handtekening over de ruwe body vóór het parsen; replay-sleutel per event; "
        "contactcenter op /webhooks/contact-center/{kind} (alias /webhooks/wxcc)",
    ),
    SharedPart(
        Family.PLATFORM,
        "Secrets",
        "crypto.py · db/credentials · db/sealed",
        "AES-256-GCM met HKDF-sleutel, recordnaam als associated data, sleutel-id per waarde en "
        "rekey bij rotatie; alleen-schrijven in het portaal",
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
        "diagnostics.py · wxcc_config.py · group_check.py",
        "wxcc-check probeert elke call onschadelijk; wxcc-config-check leest de keten asset → "
        "flow → queue; de groepscontrole volgt een proef-work item (ProbeRegistry); elk verzoek "
        "staat in het verzoeklog (RequestLog)",
    ),
)

PLATE3_FOOTNOTE = (
    "Fouten van buiten worden korte codes (IntegrationError) met hooguit de reden van de "
    "provider; antwoordbodies komen nooit in logs. Elke poort heeft een fake in tests/fakes/."
)
