# Handover: groepsagenda met Webex Contact Center

Datum: 22 oktober 2026
Status: ontwerp vastgesteld op hoofdlijnen, bouw nog niet gestart
Doel van dit document: alles wat een ontwikkelaar (of Claude Code) nodig heeft om zonder deze gespreksgeschiedenis te starten.

---

## 1. Wat we bouwen, in één alinea

Een lichtgewicht boekingsdienst waarmee burgers op een eigen pagina per groep (bijvoorbeeld `afspraak.organisatie.nl/g/aangifte-2025`), waarnaar de website van de opdrachtgever linkt na zijn eigen keuzeboom, een tijdslot kiezen bij een *groep* (een skill/wachtrij in Webex Contact Center). Dezelfde pagina kan als widget in een bestaande website worden ingebed. De groep heeft eigen openingstijden, slotlengte en per tijdvak een capaciteit n (aantal beschikbare medewerkers). De burger krijgt een bevestiging per mail met .ics, de deelnemerslink naar een Webex-videogesprek, en links om te wijzigen of te annuleren. Vijf minuten voor de start maakt de dienst via Webex Connect een taak aan in de wachtrij van de groep, met de hostlink; de eerste vrije medewerker pakt hem op. Na afloop worden alle persoonsgegevens verwijderd. Beheerders beheren groepen op basis van hun rol uit de IdP.

## 2. Beslissingen die vaststaan

| # | Beslissing | Reden |
|---|---|---|
| B1 | Eigen bouw, geen Tymeslot/Cal.diy-aanpassing | Organisatiemodel en branding per organisatie ontbreken in beide; de agendaconflict-engine (hun kern) hebben wij niet nodig. Tymeslot (AGPL) dient als referentieontwerp, niet als codebron. |
| B2 | Python 3.12, FastAPI, SQLAlchemy 2, Alembic. Database: SQLite (WAL) op één VM; Postgres alleen als er ooit een tweede VM komt. Keuze is een configuratieregel. | Zo licht mogelijk; 80 boekingen/dag rechtvaardigt geen aparte databaseserver. JSON en DuckDB afgewezen (geen gelijktijdige schrijvers/transacties). |
| B3 | Jobs in een eigen tabel (`jobs`: type, run_at, booking_id, pinned_start, status, attempts), worker-lus pollt elke 30 s met `BEGIN IMMEDIATE` | Werkt op SQLite én Postgres; jobs overleven herstart; annuleerbaar per boeking. Geen Redis, geen Celery. |
| B4 | Beheer-UI server-rendered (Jinja2 + HTMX), widget als Web Component in vanilla JS | Geen frontend-framework te onderhouden. |
| B5 | Medewerker wordt nooit vastgelegd bij boeking | Capaciteit is een teller; toewijzing doet de WxCC-wachtrij op het startmoment. |
| B6 | Webex Connect is de enige brug naar WxCC én doet sms | Zit in het WxCC-abonnement; één leverancier minder. |
| B7 | Videolink via Webex Instant Connect (voorkeur) of Webex Meetings API (fallback) | Instant Connect: host-/gastlink zonder persoonlijke licentie. Zie open punt O1. |
| B8 | Drie rollen uit IdP-claims; geen eigen gebruikerstabel voor beheerders | Superuser, organisatiebeheerder, groepsbeheerder. |
| B9 | PII-verwijdering als job, 24 uur na einde slot, inclusief back-upbeleid | Zie §7. |
| B10 | Code, commits, repo-docs in het Engels; gebruikersinterface NL + EN (uitbreidbaar) | Tooling en toekomstige bijdragers. Vertaalbestanden via Babel/gettext. |
| B11 | Geen Google reCAPTCHA; rate limiting + honeypot + optioneel Cloudflare Turnstile op eigen domein | Geen call naar derde land vanaf een overheidspagina. |
| B12 | NL Design System componenten voor widget en beheer-UI | Toegankelijkheidsverantwoording grotendeels overgenomen. |
| B13 | Widget in shadow DOM met design-tokens: leest `--booking-*` CSS-variabelen van de gastpagina, erft lettertype, stelt onderdelen bloot via `::part()`; NLDS-tokens als standaard | Widget volgt de branding van de website waarop hij staat, zonder dat de CSS van die website de widget kan breken. Mails gebruiken de organisatiebranding uit het beheer. |
| B14 | Standaard: standalone pagina per groep op eigen (sub)domein in de huisstijl van de organisatie; embed is optie. Geen routeringsstap in deze dienst: de keuzeboom is van de opdrachtgever, wij zijn het eindstation per URL. | Referentie: afspraken.belastingdienst.nl (keuzeboom) → videobellen.belastingdienst.nl/{slug} (agenda, 24sessions; product wordt beëindigd). Geen CSP/CORS-afstemming nodig in de standaardvariant. |
| B15 | Formulier vraagt alleen e-mail, naam, telefoon (telefoon optioneel per groep). Geen BSN, geen DigiD. | Minimale PII; bevestigd door het referentievoorbeeld. |
| B16 | Veiligheid wordt geborgd door een niet te omzeilen CI-pijplijn, verplichte menselijke review per PR met vaste checklist, en externe toetsing (pentest, toegankelijkheidsonderzoek). Claude Code merget alleen op expliciete instructie van de product owner, bij groene CI (ADR 0012). | Gegenereerde code is vlot en ziet er goed uit; het risico is dat niemand hem leest. Zie §12. |

## 3. Domeinmodel

```
Organisation      (id, name, branding: logo, accent, sender_name, sender_email, locale_default)
Group             (id, organisation_id, name, slug, wxcc_queue_id, slot_minutes, video_provider,
                   email_intro_nl/en, email_outro_nl/en, sms_enabled, timezone)
ScheduleRule      (id, group_id, weekday, opens, closes, capacity, valid_from, valid_to)
ScheduleOverride  (id, group_id, date, opens, closes, capacity | closed=true)   # feestdagen, ziekte
GroupPage         (group_id, title_nl/en, intro_nl/en, duration_label, phone_required: bool)
Slot              (id, group_id, start, end, capacity_snapshot)   # ontstaat bij eerste boeking
Booking           (id, slot_id, status[confirmed|cancelled|completed|no_show],
                   name, email, phone, note, locale, management_token_hash,
                   guest_join_url, host_join_url, video_room_id,
                   wxcc_task_id, created_at, cancelled_at, pii_purged_at)
Job               (id, type, run_at, booking_id, pinned_start, status, attempts, last_error)
                   types: send_confirmation, send_reminder_email, send_reminder_sms,
                   create_video_room, dispatch_wxcc_task, purge_pii
AuditEvent        (id, actor, action, entity, at)   # zonder PII
```

Regels:
- `Slot` wordt aangemaakt bij de eerste boeking in een transactie (`BEGIN IMMEDIATE` op SQLite, `SELECT ... FOR UPDATE` op Postgres) op `(group_id, start)`; capaciteit wordt op dat moment vastgepind uit `ScheduleRule`/`ScheduleOverride`. Partiële unieke index op `(group_id, start) WHERE status='live'` als backstop. (Patroon overgenomen van Tymeslot `GroupScheduling`.)
- Vol = aantal niet-geannuleerde boekingen ≥ `capacity_snapshot`. Vol slot wordt niet getoond.
- Wijzigen = nieuwe boeking in nieuw slot + oude op `cancelled`, in één transactie; videoroom en jobs worden opnieuw gemaakt.
- Elke tijdgebonden job pint `slot.start`; bij uitvoeren controleert hij of de boeking nog `confirmed` is en of de starttijd onveranderd is, anders stopt hij stil. (Patroon van Tymeslot reminder-jobs.) Bij annuleren/wijzigen worden openstaande jobs van de boeking verwijderd.
- `management_token` wordt één keer getoond in de mail-URL en alleen gehasht opgeslagen.

## 4. Externe koppelingen

| Koppeling | Richting | Wat | Status |
|---|---|---|---|
| Webex Instant Connect URL API | uit | per boeking 1 host- en 1 gastlink | O1: tokentype en vervaltijd verifiëren |
| Webex Meetings API (fallback) | uit | create/update/delete meeting op service-account; Join a Meeting voor start-/joinLink | gedocumenteerd |
| Webex Connect inbound webhook | uit | op T-5: `{booking_id, first_name, host_join_url, queue_id, locale}` | Connect-flow: Create Task → Queue Task (dynamische queue) |
| Webex Connect SMS | uit | herinnering en bevestiging per sms | Connect-flow met SMS-node |
| SMTP / EU-mailprovider | uit | bevestiging, herinnering, annulering, wijziging | per organisatie afzender |
| IdP (OIDC) | in | login beheer; claims: `role`, `org`, `groups[]` | O3: claimnamen afspreken |
| Bestaande website | in | standaard: link naar `/g/{slug}`; optioneel widget via `<script src>` + `<gov-booking group="...">` | Bij embed: CSP en CORS vastleggen |

## 5. Flows

**Boeken.** Widget → `GET /api/v1/groups/{slug}/availability?from&to` → `POST /api/v1/bookings` (rate-limited, honeypot) → transactie slot+booking → jobs: `create_video_room` (direct), `send_confirmation` (na videoroom), `send_reminder_email` (T-24h), `send_reminder_sms` (T-2h, optioneel), `dispatch_wxcc_task` (T-5m), `purge_pii` (slot.end + 24h).

**T-5 naar WxCC.** `dispatch_wxcc_task` → check boeking nog geldig → POST naar Connect-webhook → sla `wxcc_task_id` op uit antwoord → bij fout: retry met backoff (3x) en daarna alert naar beheer.

**Wijzigen/annuleren.** `GET /b/{token}` → toont boeking → `POST /b/{token}/cancel` of `/reschedule` → jobs opnieuw → mail.

**Niet opgepakt na X minuten (open punt O2).** Voorstel: Connect meldt via webhook `task.abandoned` terug; dienst stuurt sms/mail "we bellen u terug" en zet status `no_show_agent`. X per groep instelbaar, standaard 10 minuten.

## 6. Rollen en beheer

| Rol | Mag |
|---|---|
| superuser | alles: organisaties, IdP-config, SMTP, Connect-credentials, branding, alle boekingen inzien |
| org_admin | eigen organisatie: groepen, regels, overrides, routering, mailteksten, boekingen van eigen groepen |
| group_admin | eigen groepen: regels, overrides, mailteksten, boekingen inzien |

Autorisatie: één `require_scope(org_id, group_id)`-dependency in FastAPI; elke query op groepsdata filtert op de scope uit het token. Geen uitzonderingen in de code; test met negatieve cases.

Beheerscherm: eigen ingang (`/admin`), OIDC-login, server-rendered. Agenda-inzage per groep per dag/week; boekingen zonder PII na purge.

## 7. Privacy en retentie

- Bewaartermijn PII: tot `slot.end + 24h`. Job `purge_pii` nult `name, email, phone, note, guest_join_url, host_join_url`, zet `pii_purged_at`. De rij blijft voor statistiek (groep, slot, status).
- Geannuleerde boekingen: purge binnen 24 uur na annulering.
- Logs: nooit PII; structlog met een processor die bekende velden maskeert; test die dit afdwingt.
- Back-ups: SQLite via Litestream (continu) of dagelijkse `.backup`, versleuteld, retentie gelijk aan PII-termijn plus 7 dagen (vastleggen in DPIA). Een back-up bevat de PII van dat moment; dit is onderdeel van het retentiebeleid.
- Leveranciers: mail, sms (Connect), WxCC-taakcontext: retentie contractueel vastleggen; in WxCC alleen voornaam + URL meegeven.
- DPIA en verwerkersovereenkomsten: eigenaar is de opdrachtgever; deze dienst levert het gegevensstroomdiagram.

## 8. Niet-functioneel

- Volume: ~80 boekingen/werkdag, piek 20/min. Eén VM, 2 containers + Postgres volstaat.
- Beschikbaarheid: 99,5% kantoortijd; buiten kantoortijd mag de widget "gesloten" tonen.
- Toegankelijkheid: WCAG 2.2 AA (Besluit digitale toegankelijkheid, EN 301 549). axe-core in CI vangt ~40%; daarnaast verplicht: toetsenbordbediening van het kalendergrid, focusbeheer per stap, `aria-live` voor statuswijzigingen, geen tijdslimiet (2.2.1), geen puzzel-captcha (3.3.8), geen dubbele invoer (3.3.7), doelgrootte 24 px (2.5.8), `prefers-reduced-motion`. Handmatige test met NVDA (Windows) en VoiceOver (macOS/iOS), gebruikerstest met mensen met een beperking, en een gepubliceerde toegankelijkheidsverklaring vóór livegang. Het videogesprek zelf: zie O8.
- Beveiliging: zie CLAUDE.md §Security. Externe pentest vóór livegang (overheid: verplicht stellen in aanbesteding/contract).
- Hosting: EU, bij voorkeur bestaande overheidshosting of NL-provider. Geen calls naar derde landen vanuit de widget.
- Taal: NL en EN in widget en mails; code Engels.

## 9. Repo-structuur

```
booking/
  app/
    domain/        # entities, services, policies (geen framework-imports)
    adapters/      # webex_instant_connect.py, webex_meetings.py, webex_connect.py, mail.py, sms.py, idp.py
    api/           # FastAPI routers v1 (public) en admin
    admin/         # Jinja2 templates, HTMX views
    jobs/          # Procrastinate tasks
    i18n/          # gettext .po/.mo
  widget/          # Web Component, esbuild → dist/booking-widget.js
  migrations/      # Alembic
  tests/           # unit (domain), integration (db), contract (adapters met recorded fixtures), e2e (playwright)
  docs/            # ADRs, runbook, dataflow, DPIA-input
  CLAUDE.md  README.md  CONTRIBUTING.md  SECURITY.md  CHANGELOG.md
  docker-compose.yml  Dockerfile  .github/workflows/
```

## 10. Werkpakketten (volgorde)

1. Skelet: repo, CI, docker-compose (web + worker, SQLite-volume), Alembic, structlog, healthchecks, CLAUDE.md. *(2 dagen)*
2. Domein: groepen, regels, overrides, slotberekening, boeken met lock, wijzigen, annuleren; 90% unit-coverage op domain. *(5 dagen)*
3. Public API + widget: availability, booking, beheerlinks; NLDS-componenten; i18n NL/EN; axe-tests. *(5 dagen)*
4. Mail + .ics + jobs: bevestiging, herinnering, annulering, wijziging; Procrastinate; pinning-logica. *(3 dagen)*
5. Webex-adapter: Instant Connect (of Meetings API na O1); contract-tests met opgenomen antwoorden. *(2 dagen)*
6. Webex Connect: webhook T-5, retry, sms-herinnering; Connect-flow bouwen en documenteren. *(3 dagen)*
7. Beheer-UI + OIDC + rollen; agenda-inzage; groepspagina-teksten en branding per organisatie. *(5 dagen)*
8. PII-purge, back-upbeleid, audit-events, logmaskering + tests. *(2 dagen)*
9. Hardening: volledige pijplijn uit §12 groen, ZAP-scan, mutation-score ≥ 80% op domain, pentest-voorbereiding, runbook, DPIA-input. *(3 dagen)*

Totaal ca. 30 werkdagen voor één ontwikkelaar, plus reviewtijd van een tweede persoon (reken 15%). Reken 20% extra voor afstemming met contactcenterbeheer en de websitebeheerder.

## 11. Open punten (beslissen vóór WP5/6/7)

| # | Vraag | Eigenaar |
|---|---|---|
| O1 | Instant Connect: productie-token (Service App?) en vervaltijd van links. Als links < 4 weken geldig zijn → Meetings API. | technisch |
| O2 | Wat gebeurt er als de taak na X minuten niet is opgepakt? Terugbellen, herplannen, melding? | contactcenterbeheer |
| O3 | OIDC-claims voor rol/organisatie/groepen; wie beheert groepslidmaatschap in de IdP? | identity-beheer |
| O4 | Zit een medewerker in meer dan één groep (skill)? Zo ja, capaciteit per groep lager dan echte bezetting. | contactcenterbeheer |
| O5 | Branding: rijkshuisstijl-eisen per organisatie; is één logo per organisatie genoeg? | communicatie |
| O6 | WxCC-licentie: digitale kanalen en Connect inbegrepen? | contracten |
| O7 | Hosting en back-upbeleid; wie is verwerkingsverantwoordelijke voor de DPIA? | privacy officer |
| O8 | Toegankelijkheid van het videogesprek: Nederlandse live-ondertiteling in Webex beschikbaar? Tolk NGT als derde deelnemer (extra gastlink) en een vinkje daarvoor op het formulier? | dienstverlening + contactcenterbeheer |
| O9 | "Nu direct een gesprek starten" naast inplannen (zoals 24sessions bood): technisch een directe taak in de wachtrij zonder slot, maar vraagt realtime zicht op bezetting en wachttijd uit WxCC. Fase 2 of helemaal niet? | dienstverlening |
| O10 | Wie is de menselijke reviewer per PR (§12), en wie voert de externe pentest en het toegankelijkheidsonderzoek uit? Zonder antwoord geen livegang. | opdrachtgever |
| O11 | Idee, niet voor nu: een API waarmee de WxCC AI Agent (bot, chat én spraak) afspraken kan zoeken, boeken, verzetten en annuleren. Domein en flows bestaan al; de vragen zitten in identificatie van de burger zonder DigiD (referentie + e-mail/telefoonnummer, of sms-code), welke PII naar het AI-platform mag (DPIA), service-authenticatie van de bot, en slots die uitspreekbaar zijn ("dinsdag 10.00 of 14.30"). Eerst haalbaarheid van WxCC AI Agent-acties en licentie (O6) uitzoeken. | dienstverlening + technisch |

## 12. Borging van kwaliteit en veiligheid bij AI-ondersteund bouwen

Uitgangspunt: "onkraakbaar" bestaat niet. Wat wel kan: bekende fouten uitsluiten, het aanvalsoppervlak klein houden, en elke release aantoonbaar door dezelfde controles laten gaan. Het grootste risico bij gegenereerde code is niet de code zelf, maar dat niemand hem leest.

### Zes lagen
1. **Ontwerp**: threat model (STRIDE per flow) en abuse-cases per endpoint (slot vollopen door bot, token raden, verplaatsen naar slot van andere groep, PII via foutmelding). Abuse-cases worden tests.
2. **Bouw**: domein zonder framework, één autorisatiepunt, geen raw SQL, geen PII in logs; laagscheiding afgedwongen met `import-linter`.
3. **CI**: elke PR, blokkerend, niet te omzeilen (branch protection, geen `[skip ci]`, geen force-push naar main).
4. **Review**: elk PR door een mens met de checklist hieronder. Claude Code opent PR's, merget nooit.
5. **Extern**: pentest vóór livegang en jaarlijks; toegankelijkheidsonderzoek; `SECURITY.md` en `/.well-known/security.txt` voor responsible disclosure.
6. **Run**: automatische dependency-updates, dagelijkse image-scan, alerting op 401/403-pieken en op purge-achterstand.

### Pijplijn

Per PR (blokkerend):
| Controle | Tool |
|---|---|
| Lint, format | `ruff` |
| Types | `mypy --strict` |
| Laagscheiding | `import-linter` |
| Tests + coverage (domain ≥ 90%, totaal ≥ 75%), autorisatiematrix (rol × endpoint × eigen/andere groep), abuse-cases | `pytest`, `pytest-cov` |
| Statische security | `bandit`, `semgrep` (p/python, p/owasp-top-ten, p/secrets, eigen regels: geen `print`, geen PII-velden in logregels) |
| Dependencies | `pip-audit`, `uv lock --check`, `pip-licenses` tegen toegestane lijst |
| Secrets | `gitleaks` |
| Container | `hadolint`, `trivy` (CVE + misconfig), non-root, read-only fs |
| API-fuzzing | `schemathesis` tegen de OpenAPI-spec |
| Toegankelijkheid en UX | Playwright keyboard-only flow, `axe-core` nul schendingen, Lighthouse a11y ≥ 95 |
| Migraties | Alembic up én down op verse database |
| SBOM | CycloneDX + `grype` |

Bij merge naar main (extra):
| Controle | Tool |
|---|---|
| Dynamische scan | OWASP ZAP baseline tegen gestarte container |
| Testkwaliteit | `mutmut` op `app/domain/` (score ≥ 80%); vangt tests die alleen het happy path raken |
| Supply chain | image signen met `cosign`; deploy accepteert alleen gesigneerde images |

Periodiek:
| Controle | Frequentie |
|---|---|
| Renovate/Dependabot, security-updates direct | wekelijks |
| `trivy` op draaiende image, ZAP full scan tegen staging | wekelijks |
| Controle `purge_pii`-achterstand = 0 | dagelijks, met alert |
| Externe pentest | vóór livegang, daarna jaarlijks en na grote wijziging |

### Reviewchecklist (mens, elk PR)
1. Welk endpoint of welke job is nieuw of gewijzigd, en staat `require_scope` erop?
2. Welke invoer komt van buiten, en waar wordt die gevalideerd?
3. Kan hier PII in een log, foutmelding, URL of externe payload komen?
4. Welke test zou falen als deze wijziging een fout bevatte?
5. Is er een dependency bij, en waarom?

### Wat buiten de code georganiseerd moet worden
- Een reviewer die Python leest en de vijf vragen kan beantwoorden.
- Extern pentestbureau met het threat model als input.
- Toegankelijkheidsonderzoek met NVDA/VoiceOver en gebruikers met een beperking.
- Privacy officer voor DPIA en verwerkersovereenkomsten (Webex, mail, hosting).
- Eigenaar van productiesecrets en rotatiebeleid.

### Aanbevolen: repo-skill `security-review`
Een skill in `.claude/skills/security-review/` die bij elk PR de vijf reviewvragen beantwoordt en de abuse-cases naloopt vóór de mens kijkt. Maakt de machinereview consistent en de mensenreview korter.

## 13. Referentiemateriaal uit Tymeslot (ontwerp, geen code overnemen)

- `lib/tymeslot/meetings/group_scheduling.ex` — slot-lock en capaciteitssnapshot
- `lib/tymeslot/notifications/scheduling_rules.ex`, `emails/email_scheduler/meeting_scheduler.ex` — reminder-jobs met gepinde starttijd en unieke sleutels
- `lib/tymeslot/meetings/participant_schema.ex` — management-token gehasht
- `lib/tymeslot/workers/webhook_worker.ex`, `webhooks/payload_builder.ex` — HMAC-handtekening, delivery-id, backoff
- `lib/tymeslot/integrations/video/providers/provider_behaviour.ex` — provider-interface met rolgebonden join-URL

Bronnen: Webex Instant Connect Meeting Links (developer.webex.com/docs/instant-connect-meeting-links), Webex Connect WxCC task nodes (help.webexconnect.io), Webex Meetings API Join a Meeting.
