# Contactcenter inrichten

Dit onderwerp is voor de beheerder van het contactcenter van de organisatie. Het zet de
koppeling tussen OpenPlanner en Webex Contact Center (WxCC) op, stap voor stap, één scherm per
stap: kort voor de start van een afspraak komt dan een *work item* met de link naar het
videogesprek in de wachtrij van het team, en de eerste vrije medewerker pakt het op.

Wie wat doet:

- **De beheerder van Webex en het contactcenter** doet stap 1 tot en met 6 in het
  Webex-ontwikkelaarsportaal en in Control Hub.
- **De organisatiebeheerder** (of de groepsbeheerder) doet stap 7, 9 en 10 in het portaal van
  OpenPlanner, op het tabblad **Instellingen** van de groep.
- **De superuser of het hostteam** van OpenPlanner zet de tokens van de Service App op de
  server en registreert de webhooks (stap 1 en 8), en plakt het token van de bot (stap 11).

Control Hub is Engelstalig; de namen van knoppen en schermen staan hier zoals daar. De
technische details voor het hostteam staan in het runbook, onder *Webex meetings and Webex
Contact Center*.

## Stap 1. De Webex Service App

Scherm: **developer.webex.com → My Webex Apps → Create a New App → Service App**, daarna
**Control Hub → Apps → Service Apps**.

Maak een Service App met precies deze scopes. Bewaar het *client secret* als het getoond wordt.

| Scope | Waarvoor | Zonder |
|---|---|---|
| `meeting:admin_schedule_write` | videogesprekken aanmaken en verwijderen namens de Webex-host van de groep | geen videogesprekken |
| `meeting:admin_schedule_read` | gesprekken opvragen, meldingen over gesprekken | geen live status van het gesprek |
| `meeting:admin_participants_read` | meldingen over wie het gesprek binnenkomt | geen aantallen deelnemers |
| `cjp:task_write` | work items aanmaken en als geannuleerd markeren | geen work items |
| `cjp:task_read` | toegang tot taken, hoort bij `cjp:task_write` | – |
| `cjp:user` (optioneel) | een geannuleerd work item beëindigen | een geannuleerd item blijft open tot een medewerker het sluit |
| `cjp:config_read` | de inrichting lezen voor **Koppeling controleren**, de staat van een taak opvragen | de controle en het nalopen van taken werken niet |
| `cjp:config_write` | het abonnement op taakmeldingen aanmaken (stap 8) | OpenPlanner hoort niet of een medewerker het item oppakt |

Een beheerder van Control Hub keurt de app goed onder **Apps → Service Apps**. Die beheerder
heeft de rol *Contact Center administrator* nodig, en voor de meldingen over gesprekken ook
*site admin* van de Webex-site of *Compliance Officer*. Work items (beta) moet voor de
organisatie zijn aangezet door Cisco.

Daarna genereert u in het ontwikkelaarsportaal de tokens. Het client id, het client secret en
het *refresh token* gaan naar het hostteam; die zetten ze op de server (geheimen staan nooit in
het portaal). Een scope later toevoegen betekent: opnieuw goedkeuren en nieuwe tokens.

Controleer de app met **Service App controleren** op **Koppelingen** (alleen de superuser, zie
[Koppelingen](integrations.md)). Elke regel hoort **OK** te zijn; bij een 401 of 403 noemt de
toelichting de scope die waarschijnlijk ontbreekt.

## Stap 2. Het work item-kanaal

Scherm: **Control Hub → Contact Center → Digital settings → Custom Channels**.

Maak een kanaal met *Channel type* **Work item**, bijvoorbeeld `openplanner-video`. Deze naam
vult u straks bij de groep in als **WxCC work item-kanaal**, precies zo geschreven,
hoofdletters inbegrepen.

## Stap 3. De asset en het dataschema

Scherm: **Control Hub → Contact Center → Digital settings → Assets**.

Maak op dat kanaal een asset met een eigen **Business address**, bijvoorbeeld
`afspraken@voorbeeld.nl`. Dat adres wordt het **WxCC business address** van de groep. Geef de
asset onder **Data schema** precies deze velden:

| Field key | Display name (vrij) | Viewable | Required |
|---|---|---|---|
| `bookingReference` | Afspraak | Yes | **Yes** |
| `firstName` | Voornaam | Yes | No |
| `startTime` | Starttijd | Yes | No |
| `groupName` | Team | Yes | No |
| `meetingLink` | Videogesprek | Yes | No |
| `kind` | Soort | Yes | No |

Alleen `bookingReference` is verplicht: een annulering stuurt verder niets mee. Elk ander
verplicht veld laat WxCC de work items van OpenPlanner weigeren.

Eén asset voor alle groepen is het eenvoudigst: elke groep krijgt hetzelfde kanaal en business
address, en alleen de routeringswaarde (stap 5 en 7) verschilt.

## Stap 4. Het entry point

Scherm: **Control Hub → Contact Center → Customer experience → Channels**.

Maak een kanaal van type **Work item** dat naar de asset van stap 3 wijst, zet *Status* op
actief en kies de *Routing flow* van stap 5.

## Stap 5. De flow

Scherm: **Control Hub → Contact Center → Customer experience → Flows**, daarna **Flow
Designer**.

De flow heeft drie onderdelen die OpenPlanner nodig heeft:

1. **Routering naar de juiste wachtrij.** Een flowvariabele `openplannerGroup` (type String).
   OpenPlanner vult die met de **Routeringswaarde voor de WxCC-flow** van de groep (leeg: het
   webadres van de groep). Zet er een **Case** op met per routeringswaarde een tak naar een
   **Queue Contact** met de wachtrij van die groep, en een standaardtak naar een
   reservewachtrij.
2. **Een tak voor de proef.** Zet vóór de wachtrij een **Case** op `kind` met een tak `probe`
   die direct naar **End Flow** (of *Disconnect Contact*) gaat. **Koppeling controleren** stuurt
   een proef-work item met `kind` = `probe` en wacht tot het is aangemaakt en beëindigd. De
   waarde `probe` ligt vast; de flow en OpenPlanner moeten het daarover eens zijn.
3. **Screen Pop.** Een **Screen Pop** met als URL `meetingLink`, zodat de medewerker het
   videogesprek met één klik opent.

Publiceer de flow na elke wijziging. In plaats van met de hand bouwen kan het hostteam een
voorbeeldflow laten genereren om te importeren (zie *Voorbeeldflow om te importeren* in
[Groepsinstellingen](group-settings.md)).

## Stap 6. De wachtrijen

Scherm: **Control Hub → Contact Center → Customer experience → Queues**, en voor de
medewerkers **User management → Multimedia profiles**.

Per team een wachtrij met *Channel type* **Work item**, met het team van medewerkers eraan
gekoppeld en een RONA-tijd. Het *multimedia profile* van de medewerkers moet work items
toestaan. Geef de wachtrij een naam waar de routeringswaarde van de groep in staat
(bijvoorbeeld `ACME_video_vergunningen` bij routeringswaarde `vergunningen`): dan toont de
controle per groep welke wachtrij hij gebruikt.

## Stap 7. De velden van de groep in OpenPlanner

Scherm: **Groepen → groep → Instellingen**, onderdeel **Koppelingen: video en contactcenter**.

| Veld | Wat u invult |
|---|---|
| **Hoe afspraken bij medewerkers komen** | **Contactcenter** |
| **Doorzetten naar de wachtrij (minuten vooraf)** | hoe lang voor de start het work item komt, standaard 5 |
| **Webex-host (e-mailadres)** | een functioneel Webex-account met licentie, bijvoorbeeld `videogesprekken@voorbeeld.nl` |
| **WxCC work item-kanaal** | de naam uit stap 2 |
| **WxCC business address** | het adres uit stap 3 |
| **Routeringswaarde voor de WxCC-flow** | de waarde van de Case-tak uit stap 5; leeg is het webadres van de groep |

Sla op. De uitleg per veld staat achter de (i) bij het veld en in
[Groepsinstellingen](group-settings.md).

## Stap 8. De webhooks registreren

Scherm: geen; dit doet het hostteam op de server, met `register-webhooks` (runbook,
paragraaf 4). De uitslag ziet de superuser op **Koppelingen**, onder **Registraties bij Webex en
WxCC**.

Via de webhooks hoort OpenPlanner dat een gesprek begint en eindigt, en dat een work item
binnenkwam, werd opgepakt of werd beëindigd. Het hostteam registreert opnieuw nadat een groep
een andere Webex-host kreeg, na een adreswijziging van OpenPlanner, en na een storing waarbij
WxCC het abonnement op *inactive* zette. Alle registraties horen **Actueel** te zijn en onder
**Ontbreekt** hoort niets te staan.

## Stap 9. Koppeling controleren

Scherm: **Groepen → groep → Instellingen**, kaart **Koppeling controleren**, knop **Nu
controleren**.

De controle doet voor deze ene groep wat een echte afspraak doet: er wordt een proefmeeting
ingepland en meteen verwijderd, de inrichting in Control Hub wordt gelezen, en er gaat een
proef-work item door de flow, met de voornaam "Probe" en zonder gegevens van een deelnemer.
Elke regel zegt **OK**, **Waarschuwing**, **Fout** of **Overgeslagen**, met wat u moet
veranderen. Sla wijzigingen eerst op: de controle gebruikt de opgeslagen instellingen. De
laatste uitslag blijft staan; daarna controleert het platform de groep vanzelf, standaard eens
per dag.

| Onderdeel | Wat de regels zeggen | Bij een fout |
|---|---|---|
| **Koppelingen** | of er een Service App is | stap 1 |
| **Webex (videogesprek)** | of de host bestaat en een licentie heeft, of de proefmeeting en het proefgesprek lukken | het antwoord van Webex staat op de regel; controleer de host en de site-instellingen |
| **Webex Contact Center (routering naar de queue)** | regio, kanaal, asset, dataschema, entry point, flow (`openplannerGroup`, een tak voor de routeringswaarde, de tak `probe`), wachtrijen, naam van de wachtrij, Screen Pop, en het proef-work item | stap 2 tot en met 6; de link bij de regel opent de uitleg |
| **Webhooks (terugmeldingen naar OpenPlanner)** | of het webhookadres https is, of de webhooks en het abonnement actief zijn op het huidige adres, en of een ondertekend testbericht aankomt | stap 8 |
| **Achtergrondtaken (worker)** | of de worker draait, welke versie, of hij videogesprekken en work items maakt, en of er taken van de groep mislukt zijn of te laat zijn | waarschuw het hostteam |

De regels over het proef-work item:

- **aangemaakt en beëindigd**: token, scopes, asset, flow, abonnement en webhook werken in één
  keer. Dit is het doel.
- **geen tak voor kind 'probe'** (waarschuwing): OpenPlanner beëindigt de proef zelf. Voeg de
  tak van stap 5 toe.
- **geen task:new binnen de tijd**: WxCC meldt niets terug. Kijk bij **Webhooks** in dezelfde
  controle naar het abonnement en het testbericht (stap 8).
- **kwam binnen, maar de flow beëindigde het niet**: de tak `probe` werkt niet; controleer de
  Case op `kind`.
- **kon niet worden beëindigd**: beëindig het in Agent Desktop, of voeg de scope `cjp:user` toe.
- **geweigerd** of **als mislukt gemeld**: het dataschema, het entry point of de wachtrij weigert
  het; lees de reden op de regel.

Een proef die mislukt, telt mee in de gezondheid van de koppeling: het contactcenter van de
organisatie staat dan op **Onbereikbaar** (zie [Koppelingen](integrations.md)).

## Stap 10. Terugval per groep

Scherm: **Groepen → groep → Instellingen**, velden **Terugval als het contactcenter uitvalt**
en **Webex-ruimte van het team (terugval)**.

Met **Eigen wachtrij** komt een afspraak in de wachtrij van OpenPlanner, met het label
**Terugval**, als het doorzetten ervan na de herhaalde pogingen definitief mislukt, of zolang
het contactcenter van de organisatie **Onbereikbaar** is. In dat laatste geval valt de hele
groep terug. Kies dit alleen als het team de wachtrij kent en kan werken: medewerkers loggen dan
in op OpenPlanner en pakken afspraken op het tabblad **Wachtrij** op (zie
[Wachtrij](queue.md)).

Wat het team ziet:

- een melding aan de beheerders (stap 11) dat de groep terugvalt;
- bovenaan de **Wachtrij** de banner **Terugval actief**, met sinds wanneer;
- bij elke afspraak in de wachtrij en in de agenda het label **Terugval**;
- met een **Webex-ruimte van het team**: daar plaatst de bot van de organisatie een bericht dat
  de groep terugvalt, met een link naar de wachtrij, en per afspraak die in de wachtrij komt één
  regel met de begintijd, de voornaam en dezelfde link. Zonder ruimte gaan de meldingen alleen
  naar de adressen van stap 11.

Is het contactcenter weer een tijd in orde (instelling *Hersteld na zoveel minuten zonder fouten*, standaard tien minuten),
dan eindigt de terugval vanzelf en gaan afspraken die nog niet zijn opgepakt alsnog naar het
contactcenter. Wie de afspraken van de groep mag wijzigen, kan de terugval ook zelf beëindigen
met **Terugval beëindigen** op de **Wachtrij**. Beide staan in het logboek. De deelnemer merkt
niets: de link, de wachtkamer en de regels voor annuleren en verplaatsen blijven gelijk.

### Privacy bij terugval

Tijdens een terugval komen de **voornaam en de begintijd** van een afspraak in de Webex-ruimte
van het team. Die berichten blijven daar staan, ook nadat OpenPlanner de gegevens van de
afspraak heeft verwijderd: OpenPlanner kan geen berichten in Webex verwijderen. Ze vallen onder
de bewaarregels van de organisatie voor Webex. De link naar het gesprek komt nooit in de ruimte:
die krijgt alleen wie de afspraak in de wachtrij oppakt. Kies daarom een ruimte met alleen de
medewerkers van het team. De organisatie kiest hiervoor door de terugval met een ruimte aan te
zetten.

## Stap 11. De bot voor meldingen

Scherm: **developer.webex.com → My Webex Apps → Create a New App → Bot**, daarna in OpenPlanner
**Organisaties → organisatie → Deelnemers → Kanalen**, kaart **Meldingen aan beheerders**
(alleen de superuser).

Meldingen over de koppeling (verstoord of onbereikbaar, taken die niet terugkwamen, een
mislukte proef, mislukte taken, de terugval) gaan naar een e-mailadres van de beheerders of naar
een Webex-ruimte. Voor een Webex-ruimte:

1. Maak in het Webex-ontwikkelaarsportaal een **bot**, met alleen de scope
   `spark:messages_write`. Gebruik niet de Service App van stap 1: de bot mag alleen berichten
   sturen, geen gesprekken of taken maken.
2. Voeg de bot toe aan de Webex-ruimte van de beheerders (en aan de ruimte van elk team met een
   terugval, stap 10).
3. Plak het token van de bot bij **Webex-bot** in het veld **Token van de bot** en kies
   **Opslaan**. Het token wordt versleuteld bewaard en nooit meer getoond.
4. Kies onder **Adres toevoegen** de **Soort** **Webex-ruimte**, vul bij **Adres of ruimte-id**
   het id van de ruimte in, kies bij **Vanaf** welke meldingen, en kies **Toevoegen**.
5. Kies **Testmelding sturen** bij het adres. Het portaal meldt *De testmelding is verstuurd.*
   en het bericht staat in de ruimte; zo niet, controleer dan het token van de bot en het
   ruimte-id.

Een melding bevat alleen codes, de organisatie, de groep, het tijdstip en een link naar het
portaal, nooit gegevens van deelnemers. Meer over herhalen en tellen staat in
[Kanalen](channels.md).
