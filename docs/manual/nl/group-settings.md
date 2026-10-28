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

<!-- field: queue_change_working_days -->
### Wijzigen tot (werkdagen vooraf, eigen wachtrij)

Alleen bij de eigen wachtrij. Tot hoeveel werkdagen voor de start de burger de afspraak kan
wijzigen of annuleren, van 1 tot 5, op dezelfde tijd van de dag. Werkdagen zijn maandag tot en
met vrijdag; feestdagen tellen hier als werkdag. Voorbeeld met 1: een afspraak op maandag om
10:00 kan tot vrijdag 10:00 worden gewijzigd. Een afspraak die later wordt geboekt, kan de
burger niet meer zelf wijzigen.

<!-- field: dispatch_lead_minutes -->
### Doorzetten naar de wachtrij

Hoeveel minuten voor de start de afspraak als taak naar Webex Contact Center gaat, van 0 tot 60.
Vanaf dat moment kan de burger de afspraak niet meer wijzigen of annuleren via de link in de
bevestiging.

Kies dit ruim genoeg dat een medewerker de taak kan oppakken voordat de burger in het
videogesprek wacht, bijvoorbeeld 5 minuten.

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

<!-- field: direct_call_enabled -->
### Direct gesprek starten

Met **Ja** kunnen burgers tijdens een tijdblok met medewerkers meteen een videogesprek starten,
zonder afspraak. De afsprakenpagina toont dan een knop. Buiten de tijdblokken, op feestdagen
of tijdens een noodsluiting verdwijnt die knop vanzelf.

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
