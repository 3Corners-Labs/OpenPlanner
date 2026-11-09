# API-koppelingen

Bots van het contactcenter, zoals de Webex AI Agent, gebruiken de machine-API om vrije tijden
op te vragen. Op dit scherm bepaalt de superuser welke bots mogen koppelen en wat elke bot mag
bereiken. U opent het met **API-koppelingen** in het menu. Alleen de superuser ziet het. Een
bot ziet nooit persoonsgegevens. Hoe een bot koppelt, staat in `docs/agent-api.md`.

## Schakelaars

- **API aan** is de noodstop. Staat die uit, dan worden er geen tokens meer uitgegeven en
  bestaande tokens (hoogstens 10 minuten geldig) worden meteen geweigerd, voor alle bots.
- **Documentatie publiceren** maakt de OpenAPI-specificatie en de documentatiepagina
  bereikbaar. Het adres staat naast de schakelaar. Staat die uit, dan zijn ze niet bereikbaar.

Is de API in de omgeving niet ingeschakeld, dan zegt het scherm dat. U kunt clients alvast
voorbereiden; ze werken zodra de beheerder van de server de API inschakelt.

## De lijst

Elke client heeft een rij met zijn naam en client-ID, zijn organisatie en teams, zijn rechten,
of hij aan staat, tot wanneer zijn secret geldig is en wanneer hij voor het laatst een token
vroeg. **Verloopt binnenkort** verschijnt als het secret binnen 30 dagen afloopt. Een naam
leidt naar de pagina van de client.

Boven de tabel zoekt u op naam of client-ID en, bij clients van meer organisaties, kiest u een
organisatie. Het aantal zegt hoeveel clients er passen en hoeveel er in totaal zijn. De
zoektekst komt niet terug op het scherm; de pagina meldt alleen dat er gezocht wordt.

## Een nieuwe client

Kies **Nieuwe API-client**.

<!-- field: name -->
### Naam

Voor uzelf: bijvoorbeeld de naam van de bot in Webex. Hoogstens 80 tekens.

<!-- field: organisation -->
### Organisatie

Een client hoort bij één organisatie. Dat kan later niet meer veranderen.

<!-- field: lifetime_days -->
### Geldigheid van het secret

Hoe lang het client-secret geldig is: 90, 180 (standaard) of 365 dagen. Maak vóór het einde een
nieuw secret; daarna geeft de API geen tokens meer aan deze client.

<!-- field: scopes -->
### Rechten

Wat de client mag doen. Minstens één is nodig:

- **Vrije tijden lezen** (`availability:read`): vrije tijden van groepen opvragen.
- **Gebruikscijfers per uur lezen** (`reports:read`): de aantallen en sommen van de rapporten voor de groepen van de client, zoals in `docs/reporting-api.md`. Kleine aantallen worden weggelaten en er zijn geen persoonsgegevens.
- **Configuratiepakket exporteren en importeren (hele installatie)**
  (`configuration:transfer`): geldt voor de hele installatie, ongeacht de organisatie van de
  client.

<!-- field: groups -->
### Teams

Optioneel. Vink de teams aan die de client mag bereiken. Niets aangevinkt betekent alle teams
van de organisatie.

<!-- field: networks -->
### Toegestane netwerken

Optioneel. Eén IP-adres of netwerk per regel, bijvoorbeeld de uitgaande adressen van uw
WxCC-regio. Leeg betekent elk adres.

Met **Client aanmaken** wordt de client geregistreerd. De volgende pagina toont het
**client-ID**, het **client-secret**, de einddatum en de **token-URL**. Het secret ziet u alleen
nu: kopieer het naar de kluis van het contactcenter. OpenPlanner bewaart alleen een hash. Zet
het secret nooit in een e-mail, chat of ticket. Kwijt? Maak dan een nieuw secret.

## Eén client

De pagina van de client toont zijn organisatie, of hij aan staat, tot wanneer zijn secret
geldig is en wanneer hij voor het laatst een token vroeg.

- **Uitzetten** / **Aanzetten**: een client die uit staat krijgt geen tokens. Uitzetten trekt
  al zijn tokens meteen in.
- **Tokens intrekken**: trekt elk geldig token in; de bot vraagt dan een nieuw aan.
- **Nieuw secret maken**: kies een geldigheid, daarna ziet u het nieuwe secret één keer. Het
  oude secret en alle tokens werken meteen niet meer.
- **Wat de client mag bereiken**: wijzig naam, rechten, teams en netwerken en sla op. Een
  wijziging van rechten, teams of netwerken trekt de tokens van de client in. De organisatie
  blijft zoals ze is.

Noemt de toegang van de client een team dat is gearchiveerd, dan meldt de pagina dat. Zolang de
koppeling blijft staan, wordt het team niet gewist. Sla het formulier op of verwijder de client
om de koppeling te laten vervallen.

## Een client verwijderen

Een client kan alleen verwijderd worden als hij uit staat; de knop verschijnt pas dan.
Verwijderen wist de client met zijn tokens en teamkoppelingen. Het auditlog houdt zijn id.
