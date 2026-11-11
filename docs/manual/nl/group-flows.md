# Flows van een dienst

Een flow bepaalt hoe een verzoek aan een dienst wordt afgehandeld: hoe de deelnemer zich aanmeldt,
op welke manier dienst en deelnemer contact hebben, uit welke capaciteit de tijd komt en welke
vaardigheden de medewerker nodig heeft. De naam van een flow kiest u zelf; die bepaalt niets.
Alleen wat u op dit scherm instelt, bepaalt hoe de flow werkt.

U opent het scherm via **dienst → Instellingen → Flows**. Een organisatiebeheerder of superuser
kan het lezen en wijzigen. Voordat u iets wijzigt, kan het portaal u vragen opnieuw in te loggen.

## De lijst

De lijst toont per flow de naam in uw taal (de andere taal eronder), de code, hoe de deelnemer zich
aanmeldt, het contactkanaal, de capaciteitspool, de vereiste vaardigheden en de status: concept,
actief of gearchiveerd. Heeft een flow naast zijn gepubliceerde revisie een concept in bewerking,
dan staat dat er als tweede label bij. Gearchiveerde flows ziet u met het vinkje **Alleen
gearchiveerde flows**. Een dienst heeft hoogstens het aantal flows dat de superuser instelt (*Flows
per dienst*, standaard 20).

Een dienst die al bestond voordat flows er waren, heeft de **bestaande werkwijze** als flow. Die
flow staat in de lijst, volgt de instellingen van de dienst zelf en kan hier niet worden gewijzigd
of gearchiveerd.

## Een flow maken en wijzigen

Met **Nieuwe flow** maakt u een concept. Een concept mag onvolledig zijn: het controlelijstje naast
het formulier laat zien wat nog ontbreekt. Het formulier heeft vier delen en één knop,
**Concept opslaan**. Een echte fout, zoals een pool die niet meer bestaat of een standaard voor een
aanmeldweg die de flow niet aanbiedt, ziet u bij het veld zelf.

<!-- field: flow_code -->
### Code

Het adres van de flow: kleine letters, cijfers en streepjes, hoogstens 40 tekens, bijvoorbeeld
`persoonlijk-gesprek`. De code is nog niet in gebruik bij een andere flow van de dienst en kan na
het maken niet meer worden gewijzigd. De code `new` is gereserveerd.

<!-- field: label_nl -->
### Naam in het Nederlands

De naam die deelnemers lezen als de pagina in het Nederlands staat, hoogstens 80 tekens. De naam
bepaalt niet hoe de flow werkt.

<!-- field: label_en -->
### Naam in het Engels

Dezelfde naam in het Engels. Beide namen zijn verplicht.

### Aanmelden en contact

U kiest hoe de deelnemer zich aanmeldt (op afspraak, direct of allebei) en het contactkanaal:
videogesprek, telefoon, op locatie of geen gesprek. Onder het contactkanaal leest u wanneer een
verzoek gereed is voor behandeling en wat de medewerker als eerste doet. Dat volgt uit het gekozen
kanaal en staat vast; u kiest het niet zelf. Alleen bij een kanaal met meer dan één mogelijke
eerste actie (op locatie) kiest u die actie, voor zover ze al bestaat. Omdat het scherm zonder
script werkt, volgen deze gegevens het **opgeslagen** contactkanaal: sla het concept op na een
wijziging.

<!-- field: duration_minutes -->
### Verwachte duur

De lengte van één afspraak in minuten, in stappen van 5. De grenzen staan bij het veld.

### Capaciteit en vaardigheden

Een nieuwe flow put uit een **capaciteitspool** van de organisatie, niet uit de eigen bezetting van
de dienst. Pools beheert de organisatiebeheerder bij [Vaardigheden en
capaciteit](organisation-skills-capacity.md). U tikt de vaardigheden aan die iedere medewerker nodig
heeft, en kiest per onderwerp van de dienst of de flow het aanbiedt en welke extra vaardigheden dat
onderwerp vraagt. De pool moet alle vaardigheden garanderen die de flow en zijn onderwerpen
vragen; anders staat het punt *Vaardigheden* op het controlelijstje op **Ontbreekt**.

### Afhandeling

U kiest waar verzoeken naartoe gaan en wat een verzoek afrondt. Nu kan dat alleen de eigen wachtrij
en afronding door de medewerker; het contactcenter en afronding door een gebeurtenis van de
aanbieder staan in de lijst, gemarkeerd met *volgt*. U geeft aan of het formulier ook een
e-mailadres vraagt (nodig voor een bevestiging per e-mail); de naam wordt altijd gevraagd. Tot slot
kunt u de flow de **standaardflow** maken voor afspraken en voor direct. Daarvoor heeft een dienst
per aanmeldweg hoogstens één actieve flow. Zodra een flow een gepubliceerde revisie heeft, staan
zijn standaardwegen vast.

## Het controlelijstje

Naast het formulier staan zes punten: naam en aanmelden, capaciteit, vaardigheden, contactkanaal en
afhandeling, afronden, en bewaren en verwijderen. Elk punt heeft een status in woorden: **In
orde**, **Ontbreekt** of **Volgt** (dat onderdeel van OpenPlanner komt in een latere versie). De
kop zegt of de flow klaar is voor gebruik. Het lijstje beoordeelt het opgeslagen concept; staat er
geen concept, dan de gepubliceerde revisie.

## Publiceren, activeren, weggooien en archiveren

- **Concept publiceren** maakt het opgeslagen concept de gepubliceerde revisie. Een gepubliceerde
  revisie verandert niet meer; bestaande afspraken houden de revisie waaronder ze zijn gemaakt.
  Publiceren activeert de flow niet.
- **Nieuwe revisie voorbereiden** staat op een flow zonder concept en maakt een concept als kopie
  van de gepubliceerde revisie.
- **Flow activeren** wordt beoordeeld op de gepubliceerde revisie en kan alleen als alle punten van
  het controlelijstje **In orde** zijn. Zolang het bewaren en verwijderen van verzoeken zonder
  afspraak nog niet bestaat, kan geen nieuwe flow worden geactiveerd; de knop staat dan uit, met de
  uitleg.
- **Concept weggooien** vraagt eerst om bevestiging. Een flow die nooit is gepubliceerd,
  verdwijnt daarmee uit de lijst; anders blijft de gepubliceerde revisie staan.
- **Flow archiveren** vraagt eerst om bevestiging, en kan alleen op een actieve flow. Er komen dan
  geen nieuwe verzoeken meer binnen via de flow; bestaande verzoeken houden hem.

Elke wijziging komt in het [auditlog](audit.md), met de code van de flow en het nummer van de
revisie, nooit met de naam.
