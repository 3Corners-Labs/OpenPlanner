# Vaardigheden en capaciteit

Op dit scherm staan twee lijsten van een organisatie: de **vaardigheden** en de
**capaciteitspools**. Een vaardigheid zegt welke deskundigheid nodig is om een verzoek af te
handelen, zoals intake of juridisch advies. Een capaciteitspool is de tijd waaruit flows putten,
samen met de vaardigheden die iedere getelde medewerker van de pool heeft. U opent het scherm
via **Organisaties → organisatie → Overzicht → Vaardigheden en capaciteit**.

Iedereen die de organisatie mag openen, kan de twee lijsten lezen. Het formulier om iets toe te
voegen en de links om te wijzigen en te archiveren zijn er alleen voor wie dat recht heeft:
vaardigheden beheren is voorbehouden aan organisatiebeheerders en de superuser (elk voor hun
eigen organisaties), capaciteitspools beheren ook. Voordat u iets wijzigt, kan het portaal u
vragen opnieuw in te loggen. Het oude adres van de capaciteitspools stuurt u door naar dit
scherm.

## Vaardigheden

Een vaardigheid geeft geen rechten en geen inzage: rechten bepalen wat iemand mag zien,
vaardigheden bepalen welke verzoeken iemand mag oppakken.

De lijst toont elke vaardigheid met haar naam in uw taal (de andere taal eronder), haar code,
hoeveel medewerkers haar hebben en waar ze wordt gebruikt: in hoeveel capaciteitspools en flows.
Vaardigheden die u heeft gearchiveerd, ziet u met het vinkje **Alleen gearchiveerde
vaardigheden**. Een organisatie kan een beperkt aantal vaardigheden hebben dat niet is
gearchiveerd; de superuser stelt dat maximum in (Geavanceerde instellingen, Medewerkers).

### Een vaardigheid toevoegen

<!-- field: skill_code -->
#### Code

De code is hoe de vaardigheid wordt herkend in rapporten en in de configuratie: kleine letters,
cijfers en streepjes, hoogstens 40 tekens, bijvoorbeeld `intake` of `juridisch-advies`. Hij moet
nog vrij zijn in uw organisatie. U kunt de code niet meer wijzigen nadat u de vaardigheid heeft
toegevoegd; de pagina toont hem dan als vast.

<!-- field: label_nl -->
#### Naam in het Nederlands

De naam die medewerkers zien als hun portaal Nederlands is. Hoogstens 80 tekens.

<!-- field: label_en -->
#### Naam in het Engels

De naam die medewerkers zien als hun portaal Engels is. Hoogstens 80 tekens. Beide namen zijn
verplicht en u kunt ze altijd wijzigen.

### Een vaardigheid archiveren

**Archiveer** opent een pagina die zegt wat er gebeurt en om bevestiging vraagt. Na het
archiveren kan geen nieuwe eis en geen nieuwe toekenning de vaardigheid nog gebruiken. Verzoeken,
eisen en toekenningen die haar al hebben, houden haar, en werk dat al is aangenomen verandert
niet. Een gearchiveerde vaardigheid telt niet mee voor het maximum en is niet meer te wijzigen.
Verwijderen kan niet.

### Vaardigheden aan medewerkers geven

Dat doet u op de pagina van de medewerker: **Medewerkers → persoon → Vaardigheden**. Een
wijziging geldt vanaf het volgende verzoek dat wordt opgepakt of gestart. Elke wijziging op dit
scherm en op de pagina van een medewerker komt in de [auditlog](audit.md), met de codes van de
vaardigheden en nooit met een naam.

## Capaciteitspools

Per pool toont de lijst de vaardigheden die iedere getelde medewerker heeft en de flows die
eruit putten. Putten ook flows van andere diensten uit een pool en mag u die diensten niet
zien, dan zegt de lijst "en N flows van andere diensten" en niets meer.

### Soorten pools

- **Bestaand**: de bezetting van een dienst zelf, ongewijzigd. Deze pools zijn gemaakt voor de
  diensten die er al waren. Ze staan hier alleen ter inzage, en alleen voor wie die dienst mag
  zien; u kunt ze niet wijzigen of archiveren.
- **Eigen pool**: een pool die u zelf maakte en waaruit één flow put.
- **Gedeelde pool**: een pool die u zelf maakte en waaruit meer dan één flow put.

Pools die u heeft gearchiveerd, ziet u met het vinkje **Alleen gearchiveerde capaciteitspools**.
Een organisatie kan een beperkt aantal eigen en gedeelde pools hebben dat niet is gearchiveerd;
bestaande pools tellen niet mee. De superuser stelt dat maximum in.

### Let op bij het tellen

Een pool belooft dat iedere getelde medewerker alle vaardigheden van de pool heeft. Tel dezelfde
persoon niet in twee pools op hetzelfde moment: het portaal kan dat nog niet voor u controleren.
Hoeveel medewerkers per tijdblok in een pool werken, stelt u hier nog niet in; dat volgt zodra
flows de pool gebruiken.

### Een capaciteitspool toevoegen en wijzigen

<!-- field: pool_code -->
#### Code

De code waaraan de pool wordt herkend in de configuratie en wanneer instellingen naar een andere
installatie gaan: kleine letters, cijfers en streepjes, hoogstens 40 tekens, door geen andere pool
van uw organisatie gebruikt. U kunt de code na het toevoegen van de pool niet meer wijzigen.

<!-- field: pool_name -->
#### Naam van de pool

De naam waaraan u de pool herkent, hoogstens 80 tekens. Een andere pool die niet is gearchiveerd,
mag niet dezelfde naam hebben, zodat u ze uit elkaar kunt houden; u kunt een pool altijd een andere
naam geven zonder dat de code verandert.

Onder de naam vinkt u de vaardigheden aan die iedere getelde medewerker heeft. Alleen
vaardigheden die niet zijn gearchiveerd, zijn aan te vinken; een gearchiveerde vaardigheid die de
pool al waarborgt, blijft zichtbaar, als gearchiveerd gemarkeerd, zodat u haar kunt uitvinken.
Een flow die uit de pool put, mag alleen vaardigheden eisen die hier zijn aangevinkt.

### Een capaciteitspool archiveren

**Archiveer** opent een pagina die zegt wat er gebeurt en om bevestiging vraagt. Een pool waaruit
nog een flow put, in een concept of een gepubliceerde versie, kunt u niet archiveren: de pagina
noemt dan de flows. Haal de pool eerst uit die flows. Een gearchiveerde pool is niet meer te
wijzigen. Elke wijziging komt in de [auditlog](audit.md).
