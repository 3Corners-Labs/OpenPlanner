# Logbestemmingen

Op dit scherm koppel je het log van het platform aan een logplatform: een syslogserver, Splunk,
Elastic of een HTTP-adres dat JSON-regels aanneemt. Open het via **Platform → Logbestemmingen**.
De superuser maakt, wijzigt, test, zet aan en uit en verwijdert bestemmingen, en heeft daarvoor
een recente login nodig; de auditor ziet de lijst en de afleverstatus, nooit een token.

Een bestemming is een **kopie**. De standaarduitvoer van de containers werkt door zoals voorheen,
dus `docker compose logs` en een logagent op de host merken er niets van. Een wijziging bereikt
de web en de worker binnen ongeveer 15 seconden. Regels worden op de achtergrond in groepen
afgeleverd: een trage of falende bestemming vertraagt nooit een boekingspagina of een taak.
Blijft een platform weg, dan blijven de nieuwste regels bewaard tot een grens en vallen de oudste
weg; het scherm telt ze.

## Een bestemming toevoegen

Kies de soort, vul het adres in en, als de soort er een heeft, het token of de sleutel. De
bestemming begint **uit en ongetest**. Kies eerst **Testregel sturen**: één regel gaat direct
naar het platform en de pagina zegt of hij is aangekomen, met de statuscode van het platform maar
nooit de eigen tekst ervan. Kies daarna **Aanzetten**.

<!-- field: name -->
## Naam

Een korte naam voor de lijst, bijvoorbeeld `Splunk productie`. Namen zijn uniek.

<!-- field: kind -->
## Soort

- **Syslog**: RFC 5424-berichten, met octet counting over TLS (aanbevolen) of TCP, of als
  datagrammen over UDP. De JSON-regel is het bericht; de applicatienaam is `openplanner` en de
  facility `local0`.
- **Splunk (HTTP Event Collector)**: één event per regel, met tijd, index en bron.
- **Elastic of OpenSearch**: de `_bulk`-API in een datastream.
- **HTTP (JSON-regels)**: een `POST` van JSON gescheiden door regeleinden, voor een
  OpenTelemetry-collector, Logstash of Fluent Bit.

<!-- field: address -->
## Adres

`https://host/pad` voor de HTTP-soorten; `syslog+tls://host:6514` voor syslog (ook
`syslog+tcp://` en `syslog+udp://`, die voor ontwikkeling zijn: een productie-installatie
weigert ze, en gewone `http://`). In het adres staat geen gebruikersnaam, wachtwoord of query;
het token heeft een eigen veld. Bij Splunk is het pad standaard `/services/collector/event`.

<!-- field: secret -->
## Token of sleutel

Het Splunk HEC-token, de Elastic API-sleutel of een bearer-token. Het wordt versleuteld
opgeslagen, nooit meer getoond en nooit in een log of het auditlog geschreven. Laat het veld
leeg om het opgeslagen token te behouden. Syslog heeft er geen. Ontbreekt de
instellingensleutel of is hij gewijzigd, dan meldt de pagina dat het opgeslagen token niet te
openen is.

<!-- field: index -->
## Index of datastream

De Splunk-index, of de Elastic-datastream (standaard `logs-openplanner-default`). Letters,
cijfers en `- _ . : *`.

<!-- field: source -->
## Bron of hostnaam

De Splunk-bron, of de hostnaam in syslogberichten. Leeg laat hem weg.

<!-- field: min_level -->
## Laagste niveau

Gewone logregels op dit niveau en hoger. Een bestemming kan alleen **beperken** wat het platform
logt: de regels die hij kan ontvangen zijn die het logniveau toelaat. Voor tracerregels moet
het logniveau ook op TRACE staan onder **Geavanceerde instellingen** (het valt vanzelf terug
na hooguit vier uur), en moet de optie hieronder aan staan.

<!-- field: ca_pem -->
## Extra certificaatautoriteit

Voor een platform met een certificaat van een autoriteit die de server niet kent: plak het
publieke certificaat van die autoriteit (PEM). Het wordt alleen voor deze bestemming
vertrouwd. Een privésleutel wordt geweigerd.

## Welke regels een bestemming ontvangt

- **Auditgebeurtenissen** (beheeracties, systeemgebeurtenissen, beveiligingsgebeurtenissen):
  die waarvan de categorie is aangevinkt. Het niveau geldt hier niet voor, net als op de
  standaarduitvoer.
- **Diagnosegebeurtenissen**: alleen met de optie aan. Dat is foutopsporingsdetail dat wordt
  vastgelegd zolang de diagnosemodus voor een groep aan staat, en het verlaat de installatie
  alleen naar een bestemming die erom vraagt.
- **Tracerregels**: alleen met de optie aan. Elk verzoek (route, niet het adres), elke
  taakstap, uitgaande aanroep (host, niet het adres), trage databaseopdracht en paginaopbouw.
  Geen persoonsgegevens.
- **Alle andere regels**: op het laagste niveau en hoger.

Persoonsgegevens zijn in elke regel gemaskeerd, net als op de standaarduitvoer.

## De status lezen

Elk proces, de **web** en de **worker**, heeft een eigen wachtrij en meldt apart. **Verstuurd**
en **weggevallen** tellen regels; **in wachtrij** wachten nog. **Mislukt** telt mislukte
leveringen. Een falende bestemming wordt opnieuw geprobeerd met een groeiende wachttijd, tot het
maximum onder **Geavanceerde instellingen**; de eerste mislukking en het herstel staan als
systeemgebeurtenis in het auditlog. Een regel die het platform definitief weigert (hij is voor
dat platform misvormd) valt weg, zodat hij de rest niet blokkeert. Redenen: **niet bereikbaar**
(adres, poort, firewall, time-out), **geweigerd** (controleer het token), **tls** (certificaat
niet vertrouwd), **server** (het platform faalde zelf), **niet gereed** (het opgeslagen token is
niet te openen).
