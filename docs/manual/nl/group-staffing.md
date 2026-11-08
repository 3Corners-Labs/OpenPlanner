# Bezetting van een groep

De bezetting zegt wanneer de medewerkers van een groep dienst hebben en hoeveel het er zijn.
Deelnemers kunnen alleen een afspraak boeken, of een direct gesprek starten, als de bezetting
dat toestaat. Het tabblad **Planning** van een groep heeft drie onderdelen:

- **Bezetting**: de tijdblokken van de week.
- **Afwijkende bezetting**: één datum die anders is dan de week.
- **Feestdagen**: welke feestdagenkalenders voor de groep gelden.

U opent ze via **Groepen → groep → Planning** en kiest daar het onderdeel in de tweede rij.
Groepsbeheerders, organisatiebeheerders en de superuser kunnen ze wijzigen, en een eigen rol met
het recht om de bezetting te beheren. Compliance en privacy (alleen lezen) en medewerkers
kunnen dat niet. Voor het opslaan vraagt het portaal soms opnieuw om in te loggen.

Een wijziging geldt voor nieuwe boekingen. Bestaande boekingen houden altijd hun tijdslot, ook als
u daarna het aantal medewerkers verlaagt. Een tijdslot kan dan meer boekingen hebben dan
medewerkers; hoe u zo'n tekort oplost staat bij [Agenda van de groep](group-agenda.md). De drie
pagina's zeggen hoeveel tijdsloten in de komende 14 dagen tekortkomen. Elke wijziging staat in
het auditlog.

## Bezetting per week

De pagina toont de week van maandag tot en met zondag. Een dag heeft één of meer **tijdblokken**.
Een tijdblok heeft een begin, een einde, een aantal medewerkers en, als u wilt, een label.
Aansluitende tijdblokken met medewerkers worden samen verdeeld in tijdsloten van de gespreksduur
van de groep (zie [Groepsinstellingen](group-settings.md)); deelnemers boeken een tijdslot. Een
dag zonder tijdblokken heeft geen bezetting: u ziet *Geen bezetting* en niemand kan die dag
boeken.

Met **Toevoegen** onder een dag voegt u daar een tijdblok toe, of gebruik het formulier
**Tijdblok toevoegen** onder de week. Bij elk tijdblok staan **Wijzigen**, **Splitsen** en
**Verwijderen**.

<!-- field: capacity -->
### Aantal medewerkers

Het aantal medewerkers in het tijdblok, van 0 tot 1000. Zoveel deelnemers kunnen in hetzelfde
tijdslot boeken. **0** mag en betekent: niemand dienst. Gebruik het bijvoorbeeld voor lunch: de
week laat dan zien dat de pauze bewust is en niet vergeten. Een tijdblok met 0 medewerkers heeft
geen tijdsloten en geen directe gesprekken. Een tijdblok toont *geen medewerkers* in woorden,
niet alleen met een andere kleur.

<!-- field: label -->
### Label

Een optionele aantekening bij het tijdblok, zoals *Lunch* of *Teamoverleg*, hoogstens 50 tekens
en op één regel. Alleen beheerders zien het; deelnemers en logboeken krijgen het nooit.

<!-- field: valid_from -->
### Geldig vanaf en tot en met

Optioneel. Met deze data geldt het tijdblok alleen in die periode, bijvoorbeeld voor een
zomerrooster. Laat u beide leeg, dan geldt het tijdblok altijd. Twee tijdblokken op dezelfde
dag van de week mogen elkaar niet overlappen in tijd zolang hun perioden overlappen. Aansluiten
mag wel (09:00-12:00 en 12:00-13:00). Overlappen ze toch, dan weigert het portaal de wijziging
en zegt dat bij het veld.

<!-- field: offer -->
### Aanbod: afspraken of direct gesprek

Per tijdblok kiest u wat deelnemers kunnen doen:

- **Afspraken** (standaard): het tijdblok levert tijdsloten.
- **Direct gesprek**: deelnemers kunnen meteen een videogesprek starten, zonder afspraak. Het
  tijdblok levert dan geen tijdsloten en onderbreekt een reeks tijdsloten, zoals een tijdblok
  zonder medewerkers.
- **Afspraken en direct gesprek**: allebei.

Een direct gesprek kan alleen starten als het huidige moment valt in een tijdblok met
medewerkers dat directe gesprekken aanbiedt. Het maximum aantal directe gesprekken tegelijk en
de tijd waarna een gesprek als niet opgepakt telt, zijn groepsinstellingen en worden niet per
tijdblok ingesteld (zie [Groepsinstellingen](group-settings.md)). Een team kan zo 's ochtends
directe gesprekken aannemen en 's middags afspraken. Een tijdblok met 0 medewerkers biedt nooit
directe gesprekken aan.

<!-- field: split_at -->
### Een tijdblok splitsen

**Splitsen** maakt van één tijdblok twee aansluitende tijdblokken, bijvoorbeeld om een lunchpauze
midden in een ochtend te zetten. Op het formulier kiest u het tijdstip van de splitsing; dat moet
strikt tussen het begin en het einde van het tijdblok liggen. Het eerste deel houdt het aantal
medewerkers, het label, het aanbod en de geldigheidsperiode; voor het tweede deel kiest u het
aantal medewerkers en een label (bijvoorbeeld 0 en *Lunch*). Het tweede deel houdt het aanbod en
de periode van het oorspronkelijke tijdblok en eindigt waar dat eindigde.

### Een tijdblok wijzigen en verwijderen

**Wijzigen** opent het formulier met de huidige waarden. Het nieuwe tijdblok mag overlappen met
zijn eigen oude tijden, maar niet met andere tijdblokken. **Verwijderen** haalt het tijdblok
meteen weg. Veranderde er niets, dan zegt het portaal dat.

## Afwijkende bezetting

Een afwijking vervangt de tijdblokken van **één datum**. U opent haar via het tabblad
**Afwijkende bezetting**. De lijst toont de komende afwijkingen met de datum en wat er geldt:
*Geen bezetting* of de tijd met het aantal medewerkers en, als dat niet de standaard is, het
aanbod. Bij elke afwijking staan **Wijzigen** en **Verwijderen**.

<!-- field: kind -->
### Soort

- **Hele dag geen bezetting**: de groep is die datum gesloten, niemand kan boeken en er start
  geen direct gesprek.
- **Eigen tijdblok**: alleen het tijdblok dat u invult geldt die datum (van, tot, aantal
  medewerkers, aanbod en label zoals bij een tijdblok van de week). Meerdere afwijkingen op één
  datum kunnen, als ze elkaar niet overlappen. Een gesloten dag kan niet ook tijdblokken hebben.

<!-- field: day -->
### Datum

De datum van de afwijking. Een datum in het verleden wordt geweigerd. Een wijziging geldt voor
nieuwe boekingen; boekingen die al op die datum zijn gemaakt houden hun tijdslot.

## Feestdagen

Op het tabblad **Feestdagen** ziet u welke feestdagenkalenders voor de groep gelden. De
superuser beheert de kalenders; een organisatie kiest welke voor haar groepen gelden (zie
[Feestdagen van een organisatie](organisation-holidays.md)).

De pagina toont de feestdagen van het komende jaar uit de kalenders die voor de groep gelden, met
wat de groep op elke dag doet:

- **Gesloten**: de groep is gesloten; deelnemers kunnen die dag niet boeken. Met **Toch open**
  blijft de groep die dag gewoon open: de tijdblokken van de week gelden.
- **Gewoon open**: de groep hield deze feestdag open. Met **Weer sluiten** maakt u dat ongedaan.
- **Eigen afwijkende bezetting**: de groep heeft op die datum een afwijking, en die gaat altijd
  voor. Zo kan een feestdag toch eigen tijden hebben. **Bekijk afwijkende bezetting** opent het
  tabblad.

<!-- field: source -->
### Welke kalenders gelden

- **Volg de organisatie** (standaard): de groep gebruikt de kalenders die de organisatie koos. De
  pagina toont welke dat nu zijn, of zegt dat de organisatie nog niets koos.
- **Eigen keuze**: de groep vinkt zelf kalenders aan. Vinkt u er geen aan, dan heeft deze groep
  helemaal geen feestdagen.

Kies en druk op **Opslaan**. Heeft een datum een eigen afwijking, dan gaat die voor; daarna volgt
een feestdag uit een kalender die geldt en niet openbleef (gesloten), en dan de tijdblokken van
de week. Een [noodsluiting](group-closure.md) komt daar bovenop. Een feestdag heft een sluiting
nooit op.
