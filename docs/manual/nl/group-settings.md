# Groepsinstellingen

Een groep is één team dat videogesprekken met burgers voert, met een eigen afsprakenpagina
op `/g/<webadres>`. Op dit scherm stelt u in hoe die afspraken eruitzien en waar ze terechtkomen.
U opent het via **Overzicht → groep → Instellingen**. Alleen een organisatiebeheerder of
superuser kan instellingen wijzigen; voor het opslaan vraagt het portaal soms opnieuw om in te
loggen.

Wijzigingen gelden voor nieuwe afspraken. Bestaande afspraken blijven zoals ze zijn geboekt.

## Webadres

Bij het aanmaken mag u het webadres leeg laten: het portaal maakt het dan uit de naam. Wat u
zelf invult, zet het portaal om naar de regels: kleine letters, cijfers en streepjes, zonder
accenten, hoogstens 64 tekens (`FIN BLD Videogroep` wordt `fin-bld-videogroep`). Is dat adres al
in gebruik, dan ziet u het voorgestelde adres terug en kiest u een ander.

Het webadres (de *slug*) staat vast zodra de groep is aangemaakt. Websites van de organisatie
linken ernaar, dus een nieuw adres zou die links breken. Is een ander adres echt nodig, maak dan
een nieuwe groep aan.

## Planning

<!-- field: name -->
### Naam

De naam van de groep zoals burgers en medewerkers hem zien: op de afsprakenpagina, in
bevestigingen en herinneringen, en in de taak bij het contactcenter. Kies een naam die een
burger herkent, bijvoorbeeld "Vergunningen" in plaats van een interne teamcode.

<!-- field: timezone -->
### Tijdzone

Alle tijden van deze groep (tijdblokken, tijdslots, e-mails) gelden in deze zone. Zomer- en
wintertijd gaan automatisch. Voor Nederland is dat `Europe/Amsterdam`.

<!-- field: slot_minutes -->
### Duur van een gesprek

Hoe lang één afspraak duurt, in minuten: een veelvoud van 5, van 5 tot 480. De tijdblokken van
de groep worden in stukken van deze lengte verdeeld; elk stuk is een tijdslot dat een burger
kan kiezen.

Zolang er afspraken in de toekomst staan, kunt u de duur niet wijzigen. Anders zouden die
afspraken niet meer op een tijdslot passen. Wacht tot ze voorbij zijn, of verzet ze eerst.

<!-- field: min_notice_minutes -->
### Minimale aanlooptijd

Hoe lang van tevoren een tijdslot dichtgaat voor nieuwe boekingen. Bij 60 kan een burger om
10:00 niet meer boeken voor 10:30, wel voor 11:00.

Deze waarde moet minstens zo groot zijn als **Doorzetten naar de wachtrij**. Een afspraak moet
geboekt zijn voordat hij naar het contactcenter gaat.

<!-- field: horizon_days -->
### Vooruit boeken

Hoeveel dagen vooruit burgers kunnen kiezen, van 1 tot 366. Houd dit in lijn met hoe ver het
rooster van het team vaststaat: een tijdslot dat te boeken is, moet ook bemand zijn.

<!-- field: max_bookings_per_email -->
### Afspraken per e-mailadres

Hoeveel toekomstige afspraken één e-mailadres tegelijk bij dit team mag hebben, van 1 tot 10.
Standaard 3. Mensen boeken vaak voor een ander, bijvoorbeeld een ouder voor een kind of een
mantelzorger voor een naaste; zet het getal daarom niet te laag. Een afspraak die is geannuleerd
of voorbij is, telt niet mee. Hetzelfde e-mailadres twee keer op hetzelfde moment kan nooit:
dat is bijna altijd een dubbele klik. De burger krijgt bij een weigering een algemene melding,
zonder dat de pagina zegt dat er al een afspraak is.

<!-- field: dispatch_kind -->
### Hoe afspraken bij medewerkers komen

- **Contactcenter**: kort voor de start (zie **Doorzetten naar de wachtrij**) komt de afspraak
  als taak in de wachtrij van het team in het contactcenter. De eerste vrije medewerker pakt hem
  op. Dit is de standaard.
- **Eigen wachtrij van OpenPlanner**: voor teams zonder contactcenter. Medewerkers loggen in op
  OpenPlanner en pakken een wachtende afspraak op. De burger krijgt altijd de eerste vrije
  medewerker. Er gaat geen taak naar het contactcenter, ook niet als de velden hieronder zijn
  ingevuld.

Een wijziging geldt voor afspraken die nog niet zijn doorgezet. Een afspraak die al als taak in
het contactcenter staat, blijft daar. Lukt het contactcenter niet (storing van Webex Connect of
Webex Contact Center), dan kunt u de groep tijdelijk op de eigen wachtrij zetten.

<!-- field: citizen_change_lead_minutes -->
### Verplaatsen door de burger tot (minuten vooraf)

Tot hoeveel minuten voor de start de burger de afspraak kan verplaatsen, standaard 60. Het
getal is nooit kleiner dan **Doorzetten naar de wachtrij** en hoogstens 1440 (een dag).
Annuleren kan altijd tot het gesprek begint; dat is vast en niet instelbaar. Het geldt voor
contactcenter en eigen wachtrij. Een afspraak die later wordt geboekt dan dit getal, kan de
burger alleen nog annuleren.

<!-- field: queue_visible_from_minutes -->
### Zichtbaar in de wachtrij (minuten vooraf, eigen wachtrij)

Alleen bij de eigen wachtrij. Hoeveel minuten voor de start een afspraak in de wachtrij van de
medewerkers verschijnt, van 0 tot 120. Standaard 15. Een afspraak blijft zichtbaar tot het
einde.

<!-- field: queue_free_pick_allowed -->
### Medewerkers mogen zelf een afspraak kiezen (eigen wachtrij)

Alleen bij de eigen wachtrij. **Nee** (standaard): medewerkers pakken altijd de volgende
afspraak op, zodat elke deelnemer de eerste vrije medewerker krijgt. **Ja**: ze kunnen ook een
andere afspraak uit de lijst oppakken. Dat is een werkafspraak binnen het team, geen
beveiliging.

<!-- field: calendar_invite_address -->
### Agenda-uitnodiging naar (eigen wachtrij)

Alleen bij de eigen wachtrij, en optioneel. Een gedeelde mailbox of verzendlijst van het team,
zoals `team-aangifte@organisatie.nl`. Per afspraak krijgt dat adres een agenda-uitnodiging met
de tijd, de voornaam van de deelnemer, het afspraaknummer en een link naar de wachtrij. Wordt
de afspraak verzet, geannuleerd of opgeschort, dan wordt de uitnodiging ingetrokken.

De link naar het gesprek staat er bewust niet in: die krijgt een medewerker pas als hij de
afspraak oppakt. De uitnodiging staat in de mailbox van het team en valt buiten de
bewaartermijn van OpenPlanner. Controleer het adres daarom goed; het staat ook op de
overzichtspagina van de groep en in het auditlog.

<!-- field: dispatch_lead_minutes -->
### Doorzetten naar de wachtrij

Hoeveel minuten voor de start de afspraak als taak naar Webex Contact Center gaat, van 0 tot 60.
De burger kan de afspraak daarna nog annuleren via de link in de bevestiging; verplaatsen kan
tot **Verplaatsen door de burger tot**.

Kies dit ruim genoeg dat een medewerker de taak kan oppakken voordat de burger in het
videogesprek wacht, bijvoorbeeld 5 minuten.

<!-- field: planner_change_min_lead_minutes -->
### Wijzigen door een planner

Tot hoeveel minuten voor de start een planner een afspraak vanuit de [agenda](group-agenda.md)
mag verplaatsen of annuleren, standaard 60. Daarna krijgt de burger geen bericht meer op tijd:
bel de burger dan. Het getal is nooit kleiner dan **Doorzetten naar de wachtrij** en hoogstens
1440 (een dag).

<!-- field: phone_mode -->
### Telefoonnummer op het formulier

Of het boekingsformulier om een telefoonnummer vraagt:

| Keuze | Betekenis |
|---|---|
| Niet gevraagd | Het formulier heeft geen telefoonveld. |
| Optioneel | De burger mag een nummer opgeven, bijvoorbeeld voor een sms-herinnering. |
| Verplicht | Zonder nummer kan de burger niet boeken. |

Vraag alleen om een nummer als het team het echt gebruikt. Het nummer wordt, net als naam en
e-mailadres, 24 uur na de afspraak gewist.

## Koppelingen: video en contactcenter

Laat deze velden leeg zolang de koppeling niet is ingericht. Afspraken werken dan gewoon, maar
zonder videogesprek of taak bij het contactcenter. De gegevens om in te vullen krijgt u van de
beheerder van Webex en Webex Contact Center.

<!-- field: video_host_email -->
### Webex-host

Het e-mailadres van een Webex-account met licentie. Op dit account worden de videogesprekken
ingepland. Medewerkers die inloggen met een account van de organisatie worden automatisch
cohost, zodat ze het gesprek kunnen leiden zonder dat de host aanwezig is.

Gebruik een functioneel account (bijvoorbeeld `videogesprekken@gemeente.nl`), geen
persoonlijk account: het moet blijven werken als een medewerker vertrekt.

Zodra u een nieuw adres opslaat, controleert OpenPlanner de koppeling meteen, net als de knop
**Koppeling controleren** onderaan de pagina: er wordt een proefmeeting ingepland en weer
verwijderd. De uitslag staat na het opslaan onder **Koppeling controleren**. Daarna
controleert het platform elke groep met een host vanzelf, standaard eens per dag. Gaat er
iets mis (geen licentie, verkeerd account, ontbrekende rechten), dan staat dat als
systeemmelding *Koppeling gecontroleerd* in het logboek van de groep, met het antwoord van
Webex eronder; die melding herhaalt zich tot het is opgelost en meldt één keer dat het weer
goed is. Zo hoort u het voordat een burger een afspraak zonder videogesprek krijgt.

De controle kijkt ook naar de **achtergrondtaken (worker)**: het onderdeel van OpenPlanner dat
de videomeetings echt aanmaakt. De proefmeeting draait in de beheeromgeving, de afspraken in de
worker. De controle meldt of de worker draait en welke versie, of hij dezelfde versie draait
als de beheeromgeving (anders is een update half doorgevoerd), of hij met de Webex-instellingen
is gestart, en hoeveel taken van deze groep mislukt zijn of te lang wachten. Een taak wacht te
lang als hij langer dan de platforminstelling *Taak achterstallig na* (standaard 10 minuten)
over zijn tijd is.

<!-- field: wxcc_business_address -->
### WxCC business address

Het adres van het *digital channel asset* in Webex Contact Center waarop taken van deze groep
binnenkomen. Meerdere groepen kunnen hetzelfde adres gebruiken; de routeringswaarde bepaalt dan
de wachtrij.

<!-- field: wxcc_channel -->
### WxCC work item-kanaal

De naam van het kanaal voor work items in Webex Contact Center, zoals de beheerder van het
contactcenter het heeft ingericht.

<!-- field: wxcc_routing_value -->
### Routeringswaarde voor de WxCC-flow

De waarde die de flow in Webex Contact Center gebruikt om de wachtrij te kiezen. Laat u het
veld leeg, dan stuurt het portaal het webadres van de groep mee. Vul alleen iets anders in als
de flow een andere waarde verwacht.

<!-- field: trial_call_enabled -->
### Proefgesprek voor de burger

Met **Ja** kan de burger vóór de afspraak in een echte Webex-meeting uitproberen of camera,
microfoon, luidsprekers en browser werken. Dat is geen aparte test op onze pagina: het is
dezelfde Webex-site en dezelfde Webex-omgeving als het gesprek zelf, zodat een probleem nu al
zichtbaar wordt. De burger zit er alleen in; er komt niemand van het team in en er wordt geen
taak aangemaakt. De link staat in de bevestiging, de herinnering, op de afspraakpagina en op de
pagina voor het gesprek. Het werkt alleen met een **Webex-host**. Het proefgesprek gebruikt de
Webex-site van de host: die moet toestaan dat deelnemers vóór de host binnenkomen. Of dat zo is,
ziet u bij **Koppeling controleren**.

<!-- field: trial_call_minutes -->
### Proefgesprek blijft open (minuten)

Hoe lang één proefgesprek openstaat, van 10 tot 60 minuten. Standaard 30. Daarna wordt de
Webex-meeting verwijderd; de burger kan een nieuw proefgesprek openen.

<!-- field: trial_calls_per_booking -->
### Proefgesprekken per afspraak

Hoe vaak één afspraak een proefgesprek mag openen, van 1 tot 5. Standaard 3. Elk proefgesprek is
een meeting in Webex; met dit getal kan één afspraak er niet onbeperkt aanmaken.

<!-- field: join_client -->
### Webex-pagina voor de deelnemer

Bepaalt welke pagina van Webex de deelnemer krijgt wanneer die vanaf onze pagina naar het gesprek gaat,
bij de afspraak en bij het proefgesprek. **Direct in de browser** (standaard) opent meteen de
webversie van Webex: op een computer hoeft de deelnemer niets te installeren en ziet de vraag
"app of browser" niet. **Webex laat de deelnemer kiezen** geeft Webex' eigen pagina, met de keuze
tussen de app en de browser; kies dit als uw deelnemers de Webex-app gebruiken of als uw organisatie
dat zo wil. Op een telefoon of tablet vraagt Webex in beide gevallen om de Webex-app: een
webversie is daar niet beschikbaar. De teksten in de bevestiging, op de pagina voor het gesprek
en op de proefgesprekpagina volgen deze keuze. Webex kan dit gedrag zelf wijzigen; ziet de
deelnemer toch de keuze tussen app en browser, zet het dan op **Webex laat de deelnemer kiezen**.

<!-- field: offer -->
### Aanbod per tijdblok

Bij elk tijdblok van de bezetting kiest u wat burgers in dat blok kunnen: **Afspraken** (de
tijdslots van het blok, de standaard), **Direct gesprek** (meteen een videogesprek starten,
zonder afspraak; het blok levert dan geen tijdslots) of **Afspraken en direct gesprek**. Een
afwijkende bezetting voor één dag heeft dezelfde keuze.

Tijdens een blok met medewerkers dat directe gesprekken aanbiedt, toont de afsprakenpagina de
knop **Nu een gesprek starten**. Buiten zulke blokken, op feestdagen of tijdens een noodsluiting
verdwijnt die knop vanzelf. De groep zelf heeft geen aan/uit-schakelaar meer: het aanbod staat
bij de tijdblokken, zodat een team bijvoorbeeld 's ochtends directe gesprekken aanneemt en
's middags afspraken.

<!-- field: not_picked_up_minutes -->
### Niet opgepakt na

Hoe lang een gesprek mag wachten zonder dat een medewerker het oppakt, van 1 tot 120 minuten.
Daarna valt het op in de overzichten. Een direct gesprek dat zo lang wacht, telt ook niet meer
mee voor het maximum hieronder.

<!-- field: direct_call_max_concurrent -->
### Maximaal aantal directe gesprekken tegelijk

Hoeveel directe gesprekken tegelijk open mogen staan: wachtend of in gesprek met een
medewerker. Meestal is dat het aantal medewerkers dat directe gesprekken aanneemt. Is het
maximum bereikt, dan ziet de burger dat alle medewerkers bezet zijn, met de keuze het later
opnieuw te proberen of een afspraak te maken.

Leeg betekent geen grens: Webex Contact Center verdeelt dan alle gesprekken. Geplande afspraken
tellen niet mee; die worden al door de tijdslots begrensd.

<!-- field: embedding_enabled -->
### Insluiten op websites

Met **Ja** kan de groep ook als widget op de website van de organisatie staan, naast de eigen
pagina van de groep. Dat werkt alleen op de websites die de organisatie heeft toegestaan onder
[Insluiten op websites](embedding.md). Met **Nee** (de standaard) is de groep alleen via haar
eigen pagina te boeken, ook als de widget op een toegestane website staat.
