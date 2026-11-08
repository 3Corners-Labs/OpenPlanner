# Herinneringen en berichtteksten

Het tabblad **Berichten** van een groep bepaalt wat deelnemers rond hun afspraak ontvangen:

- **Herinneringen**: wanneer een herinnering gaat en via welk kanaal.
- **Berichtteksten**: wat de berichten zeggen.
- **Contact**: hoe deelnemers de groep tijdens een sluiting anders bereiken (zie
  *Contactalternatieven van een team*).

U opent ze via **Groepen → groep → Berichten** en kiest het onderdeel in de tweede rij. Voor het opslaan
vraagt het portaal soms opnieuw om in te loggen.

| Onderdeel | Wie kan het wijzigen |
|---|---|
| Herinneringen | organisatiebeheerders en de superuser, en een eigen rol met het recht om herinneringen te beheren |
| Berichtteksten | groepsbeheerders, organisatiebeheerders en de superuser, en een eigen rol met het recht om teksten te beheren |

## Herinneringen

De pagina toont de herinneringsregels van de groep: het kanaal, het moment en een status. Een
regel heeft de status **Ingesteld** als zijn kanaal kan versturen en **Niet ingesteld** als dat
niet kan. Boven de lijst staat dan een melding: die berichten worden niet verstuurd tot een
superuser het kanaal instelt bij Koppelingen (zie [Kanalen](channels.md)). E-mail telt ook als
ingesteld als het platform zelf een e-mailserver heeft.

Wijzigingen gelden voor nieuwe boekingen. Herinneringen die al voor bestaande boekingen zijn
ingepland blijven staan, zodat niemand een beloofde herinnering mist. Dat geldt voor toevoegen,
wijzigen en verwijderen van een regel.

### Een herinnering toevoegen

<!-- field: channel -->
#### Kanaal

**E-mail** of **Sms**. Een sms gaat alleen naar deelnemers die een telefoonnummer hebben
opgegeven. Vraagt het boekingsformulier van de groep geen telefoonnummer, dan kunt u sms niet
kiezen; de pagina zegt dat.

<!-- field: amount -->
#### Hoeveel, en eenheid

Een heel getal en een eenheid: **minuten vooraf**, **uur vooraf** of **dagen vooraf** voor het
begin van de afspraak. Het moment ligt tussen 5 minuten en 30 dagen voor het begin. Een groep
heeft hoogstens 5 herinneringsregels. Hetzelfde kanaal op hetzelfde moment kan niet twee keer;
het portaal zegt dan dat de regel al bestaat.

De lijst toont het moment in de grootste hele eenheid: 1440 minuten staat als *1 dag vooraf*.

### Een herinnering wijzigen en verwijderen

**Wijzigen** opent het formulier met het huidige kanaal en moment. Hebt u opgeslagen zonder iets
te veranderen, dan zegt het portaal dat. **Verwijderen** haalt de regel meteen weg. Ook bij een
wijziging mogen kanaal en moment niet die van een andere regel van de groep zijn.

## Berichtteksten

Op dit tabblad wijzigt u de tekst van de berichten aan deelnemers. De opmaak, de links en de
agenda-bijlage blijven vast, en u bewerkt geen HTML of sjablonen; u past alleen de tekst aan. De
tabbladen bovenaan kiezen de taal van de teksten die u bewerkt; elke taal die de installatie
aanbiedt heeft een eigen set. Deelnemers krijgen het bericht in hun eigen taal.

Een tekst wordt in deze volgorde gekozen:

1. de eigen tekst van de groep, hier ingesteld;
2. de tekst van de organisatie, ingesteld bij [Teksten voor deelnemers](organisation-texts.md);
3. de standaardtekst van OpenPlanner.

Onder elk veld leest u of de tekst **Standaardtekst** is of **Eigen tekst van deze groep**.

### De berichten

De pagina heeft een kaart per soort bericht. Elke e-mail heeft een onderwerp en een inleiding;
de meeste hebben ook een sms-tekst.

- **Bevestiging**, **Wijziging**, **Herinnering** en **Annulering**.
- **Vraag om een nieuwe tijd te kiezen (door een planner)**, **Herinnering om een nieuwe tijd te
  kiezen** en **Afspraak vervallen zonder nieuwe tijd**: de berichten bij het verzoek van een
  planner om een nieuwe tijd (zie [Agenda van de groep](group-agenda.md)).
- **Redenen bij wijzigingen door een planner**: de drie vaste redenen waaruit een planner kiest
  (te weinig medewerkers, technische storing, onvoorziene omstandigheden). Een planner kan geen
  eigen reden intypen.
- **Alle e-mails**: de afsluitende regel.
- **Hoe u de medewerker noemt**: het woord voor de medewerker in deze groep, enkelvoud en
  meervoud. In een tekst gebruikt u daarvoor `{agent}`, `{agents}`, `{Agent}` en `{Agents}`.
  Beide woorden veranderen samen.

### Plaatsvervangers

Een tekst mag woorden tussen accolades bevatten die bij het versturen worden ingevuld, zoals
`{date}`. Onder elk veld ziet u welke dat veld mag gebruiken:

- E-mails: `{date}`, `{time}` en `{group_name}`.
- Sms: ook `{first_name}` en, bij de meeste, `{manage_url}`, de link om de afspraak te
  wijzigen. De sms bij annulering heeft `{reason}` in plaats van de link.
- Berichten over een nieuwe tijd hebben ook `{deadline}`, het moment waarop het verzoek vervalt.

U mag een plaatsvervanger weglaten, maar geen andere gebruiken. De woorden voor de medewerker
hierboven werken in elke tekst. Grenzen: een onderwerp is hoogstens 200 tekens op één regel, een
e-mailinleiding hoogstens 2000 en een sms hoogstens 320 tekens (voordat de plaatsvervangers zijn
ingevuld).

### Opslaan

Druk op **Opslaan** om de teksten van de taal die u ziet op te slaan. Eerst worden alle velden
gecontroleerd: klopt er één niet, dan wordt niets opgeslagen en zegt het veld wat mag (het
maximum en de plaatsvervangers). Maak een veld leeg, of zet de standaardtekst terug, om de
standaardtekst terug te krijgen: alleen echte wijzigingen worden bewaard. Elke wijziging staat
in het auditlog met de sleutels van de teksten en de taal, niet met de teksten zelf.
