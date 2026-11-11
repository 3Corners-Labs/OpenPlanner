# Flows van een dienst

Een flow bepaalt hoe een verzoek aan een dienst wordt afgehandeld: hoe het binnenkomt, op welke
manier dienst en deelnemer contact hebben, uit welke capaciteit de tijd komt en welke
vaardigheden de medewerker nodig heeft. De naam van een flow kiest u zelf; die bepaalt niets.
Alleen wat u op dit scherm instelt, bepaalt hoe de flow werkt.

U opent het scherm via **dienst → Instellingen → Flows**. Een organisatiebeheerder of superuser
kan het lezen en wijzigen. Voordat u iets wijzigt, kan het portaal u vragen opnieuw in te loggen.

## De lijst

De lijst toont per flow de naam in uw taal (de andere taal eronder), de code, hoe het verzoek
binnenkomt, het contactkanaal, de capaciteitspool, de vereiste vaardigheden en de status: *Nog niet
actief*, *Actief* of *Gearchiveerd*. Heeft een flow naast zijn gepubliceerde revisie een concept in
bewerking, dan staat dat er als tweede label bij (*Concept in bewerking*). Gearchiveerde flows ziet
u met het vinkje **Alleen gearchiveerde flows**. Een dienst heeft hoogstens het aantal flows dat de
superuser instelt (*Flows per dienst*, standaard 20); gearchiveerde flows en de bestaande
werkwijze tellen niet mee.

Een dienst die al bestond voordat flows er waren, heeft de **bestaande werkwijze** als flow. Die
flow staat in de lijst, volgt de instellingen van de dienst zelf en kan hier niet worden gewijzigd
of gearchiveerd.

## Concept en revisie

Een flow heeft een **status**: *Nog niet actief* (een nieuwe flow, ook als u hem al hebt
gepubliceerd), *Actief* of *Gearchiveerd*. Wat u bewerkt, is een **revisie**: een gepubliceerde
revisie verandert niet meer, en een **concept** is de revisie die u nog bewerkt. Het woord concept
gaat dus over de revisie, nooit over de status van de flow.

## Een flow maken en wijzigen

Met **Nieuwe flow** maakt u een concept. Een concept mag onvolledig zijn: het controlelijstje naast
het formulier laat zien wat nog ontbreekt. Het formulier heeft vier delen en één knop,
**Concept opslaan**. Een echte fout, zoals een pool die niet meer bestaat of een standaard voor een
aanmeldweg die de flow niet aanbiedt, ziet u bij het veld zelf. Een optie die er nog niet is,
staat in de lijst met *(volgt)* achter de naam en kunt u niet kiezen.

### Naam

<!-- field: flow_code -->
#### Code

Het adres van de flow: kleine letters, cijfers en streepjes, hoogstens 40 tekens, bijvoorbeeld
`persoonlijk-gesprek`. De code is nog niet in gebruik bij een andere flow van de dienst en kan na
het maken niet meer worden gewijzigd. De code `new` is gereserveerd.

<!-- field: label_nl -->
#### Naam in het Nederlands

De naam die deelnemers lezen als de pagina in het Nederlands staat, hoogstens 80 tekens. De naam
bepaalt niet hoe de flow werkt.

<!-- field: label_en -->
#### Naam in het Engels

Dezelfde naam in het Engels. Beide namen zijn verplicht.

### Aanmelding en contact

<!-- field: intake -->
#### Hoe komt een verzoek binnen?

U kiest *Met een afspraak*, *Direct, zonder afspraak* of allebei. Alleen de aanmeldwegen die u hier
aanzet, kunt u verderop als standaard kiezen. Sla het concept op na een wijziging.

<!-- field: interaction_kind -->
#### Contactkanaal

Videogesprek, telefoon, op locatie of geen gesprek. Videogesprek staat in de lijst met *(volgt)*:
dat kunt u nog niet kiezen. Onder het contactkanaal leest u wanneer een verzoek gereed is voor
afhandeling en wat de medewerker als eerste doet. Dat volgt uit het **opgeslagen** contactkanaal,
omdat het scherm zonder script werkt: sla het concept op na een wijziging van het kanaal.

<!-- field: handling_action -->
#### Kies de eerste actie

Alleen bij een kanaal met meer dan één mogelijke eerste actie (op locatie) kiest u de actie; een
actie die er nog niet is, staat er met *(volgt)*. Past een verstuurde actie niet bij het gekozen
contactkanaal, dan neemt het scherm de eerste actie van dat kanaal en meldt dat: *De eerste actie
is aangepast aan het gekozen contactkanaal.*

<!-- field: duration_minutes -->
#### Verwachte duur

De lengte van één afspraak in minuten, in stappen van 5. De grenzen staan bij het veld.

### Capaciteit en vaardigheden

<!-- field: capacity_pool_id -->
#### Capaciteitspool

Een nieuwe flow put uit een **capaciteitspool** van de organisatie, niet uit de eigen bezetting van
de dienst. Pools beheert de organisatiebeheerder bij [Vaardigheden en
capaciteit](organisation-skills-capacity.md). Een gearchiveerde pool die de flow al gebruikt, blijft
staan (met *(gearchiveerd)*); een andere gearchiveerde pool kunt u niet kiezen.

<!-- field: required_skills -->
#### Vereiste vaardigheden

U tikt de vaardigheden aan die iedere medewerker nodig heeft. De pool moet ze garanderen; anders
staat het punt *Vaardigheden* op het controlelijstje op **Ontbreekt**.

<!-- field: subjects -->
#### Onderwerpen in deze flow

Per onderwerp van de dienst vinkt u aan of de flow het aanbiedt. In de tabel staan daarnaast de
extra vaardigheden die het onderwerp nu vraagt (*Geen* als er geen zijn). Die kiest u op een eigen
pagina: na het opslaan van het concept staat achter een aangeboden onderwerp **Extra vaardigheden
kiezen**. Daar tikt u de extra vaardigheden aan en slaat u ze op in het concept; u komt terug bij
de onderwerpen. Een onderwerp dat u niet meer aanbiedt, verliest zijn extra vaardigheden; een
onderwerp dat aangeboden blijft, houdt ze als u het formulier opslaat.

### Afhandeling

<!-- field: dispatch -->
#### Waar het verzoek terechtkomt

Nu kan dat alleen de eigen wachtrij; het contactcenter staat in de lijst met *(volgt)*.

<!-- field: completion -->
#### Afronding

Nu kan dat alleen door de medewerker; afronding door een gebeurtenis van de aanbieder staat in de
lijst met *(volgt)*.

<!-- field: collects_email -->
#### Welke gegevens vraagt u?

Of het formulier ook een e-mailadres vraagt (nodig voor een bevestiging per e-mail). De naam wordt
altijd gevraagd.

<!-- field: defaults -->
#### Standaardflow

U kunt de flow de standaard maken voor afspraken en voor direct. Een dienst heeft per aanmeldweg
hoogstens één standaardflow. Zodra een flow een gepubliceerde revisie heeft, staan zijn
standaardwegen vast.

## Het controlelijstje

Naast het formulier staan zes punten: naam en aanmelding, capaciteit, vaardigheden, contactkanaal en
afhandeling, afronden, en bewaren en verwijderen. Elk punt heeft een status in woorden: **In
orde**, **Ontbreekt** of **Volgt** (dat onderdeel van OpenPlanner komt in een latere versie; de
zin zegt welk onderdeel). De kop zegt wat het lijstje beoordeelt en of dat in orde is: *Het concept
is volledig* of *Het concept is nog niet volledig* als er een concept is, anders *De gepubliceerde
revisie is klaar voor gebruik* of *… is nog niet klaar voor gebruik*.

## Publiceren, activeren, weggooien en archiveren

- **Concept publiceren** maakt het opgeslagen concept de gepubliceerde revisie. Een gepubliceerde
  revisie verandert niet meer; bestaande afspraken houden de revisie waaronder ze zijn gemaakt.
  Publiceren activeert de flow niet. Het concept van een flow die al actief is, wordt eerst
  beoordeeld: een concept dat niet volledig is, wordt niet gepubliceerd, zodat er nooit een
  onvolledige revisie in gebruik komt.
- **Nieuwe revisie voorbereiden** staat op een flow zonder concept en maakt een concept als kopie
  van de gepubliceerde revisie.
- **Flow activeren** wordt beoordeeld op de gepubliceerde revisie en kan alleen als alle punten van
  het controlelijstje **In orde** zijn. Er is geen knop die uitstaat: kan het niet, dan staat er een
  zin. *Publiceer eerst het concept.* als er nog geen gepubliceerde revisie is. *Nieuwe flows kunnen
  in deze versie nog niet worden geactiveerd* zolang het automatisch afsluiten en verwijderen van
  gegevens voor nieuwe flows nog niet bestaat: u kunt de flow wel voorbereiden en publiceren.
  Anders zegt de zin dat activeren kan zodra de gepubliceerde revisie op alle punten in orde is.
- **Concept weggooien** vraagt eerst om bevestiging. Een flow die nooit is gepubliceerd,
  verdwijnt daarmee uit de lijst; anders blijft de gepubliceerde revisie staan. Van een
  gearchiveerde flow kunt u geen concept weggooien.
- **Flow archiveren** vraagt eerst om bevestiging, en kan op een flow die actief is of al een
  gepubliceerde revisie heeft. Er komen dan geen nieuwe verzoeken meer binnen via de flow;
  bestaande verzoeken houden hem. Een concept dat de flow nog heeft, wordt daarbij weggegooid. Een
  flow die nooit is gepubliceerd haalt u weg door het concept weg te gooien.

Elke wijziging komt in het [auditlog](audit.md), met de code van de flow en het nummer van de
revisie, nooit met de naam.
