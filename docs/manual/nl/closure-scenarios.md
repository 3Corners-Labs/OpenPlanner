# Scenario's voor een noodsluiting

Een scenario is een voorbereide noodsluiting: de melding, een kop, contactalternatieven en een
afbeelding, klaar voor als het nodig is. Bij het sluiten kiest u een scenario en controleert u
de tekst, in plaats van onder tijdsdruk alles te schrijven en te vertalen.

U opent de scenario's via **Noodsluiting → Scenario's beheren**, voor een team of voor de hele
organisatie.

## Van wie een scenario is

- **Scenario's van de organisatie** maakt een organisatiebeheerder (of superuser). Elk team van
  de organisatie kan ze gebruiken, en bij het sluiten van de hele organisatie kiest u er een
  voor alle teams tegelijk.
- **Scenario's van een team** maakt een teambeheerder van dat team. Alleen dat team gebruikt ze.

Een teambeheerder ziet de scenario's van de organisatie en kan ze gebruiken, maar niet wijzigen
of verwijderen.

## Een scenario maken

Begin met een naam en de melding in het Nederlands en het Engels; een kop is optioneel. Op de
pagina van het scenario voegt u daarna contactalternatieven en een afbeelding toe. Het
voorbeeld bovenaan laat zien hoe de sluitingspagina er dan uitziet.

<!-- field: scenario_name -->
### Naam

Hoe beheerders het scenario herkennen, bijvoorbeeld "Storing", "Staking" of "Gebouw dicht".
Hoogstens 80 tekens. Burgers zien deze naam nooit.

### Contactalternatieven

Een scenario kan een eigen blok contactalternatieven hebben, bijvoorbeeld een ander nummer
tijdens een staking. Laat u het leeg, dan krijgt de sluiting het blok dat op dat moment voor het
team geldt (het eigen blok van het team, anders dat van de organisatie). Alles in dit blok is
openbaar: gebruik alleen algemene nummers en adressen.

### Afbeelding

Dezelfde regels als voor de afbeelding van een sluiting: PNG, JPEG of WebP van hoogstens 500 kB,
met een beschrijving in het Nederlands en het Engels, of aangevinkt als alleen versiering. De
afbeelding van een scenario is niet openbaar: alleen beheerders zien hem, tot een sluiting met
dit scenario begint. Zo lekt een voorbereide "staking" niet uit.

## Sluiten met een scenario

Kies op het scherm **Noodsluiting** bij **Beginnen met een scenario** het scenario en klik op
**Gebruiken**. Het formulier eronder is dan ingevuld met de melding en de kop van het scenario.
Pas de tekst zo nodig aan, vul het begin en het einde in en sluit het team.

Op het moment van sluiten neemt de sluiting een **kopie** van het scenario: de tekst uit het
formulier, de contactalternatieven en de afbeelding. Wijzigt of verwijdert u het scenario later,
dan blijft de lopende sluiting tonen wat burgers per e-mail kregen.

Sluit u de hele organisatie, dan kunt u alleen een scenario van de organisatie kiezen. Elk team
krijgt zijn eigen kopie en kan die daarna apart aanpassen.

## Voorbeelden om over te nemen

Nieuwe installaties hebben geen scenario's. Deze voorbeelden kunt u overnemen en aanpassen.

**Storing**

- Kop: Storing
- Melding (nl): Door een storing kunnen wij u op dit moment niet via video te woord staan. Uw
  afspraak gaat niet door. Kies een nieuwe tijd zodra wij weer open zijn, of neem op een andere
  manier contact met ons op.
- Melding (en): Because of an outage we cannot see you by video at the moment. Your
  appointment cannot go ahead. Choose a new time once we are open again, or contact us another
  way.

**Staking**

- Kop: Staking
- Melding (nl): Wegens een staking zijn wij vandaag gesloten. Uw afspraak gaat niet door. U
  krijgt bericht zodra u een nieuwe tijd kunt kiezen.
- Melding (en): We are closed today because of a strike. Your appointment cannot go ahead.
  We will let you know when you can choose a new time.

**Gebouw dicht**

- Kop: Gebouw gesloten
- Melding (nl): Ons gebouw is onverwacht gesloten. Videogesprekken gaan niet door. Wij helpen u
  graag via de contactmogelijkheden hieronder.
- Melding (en): Our building is unexpectedly closed. Video calls cannot go ahead. We are glad
  to help you through the contact options below.

## Wat wordt vastgelegd

Aanmaken, wijzigen, verwijderen, contactalternatieven en afbeelding van een scenario komen in het
auditlog, met het nummer van het scenario en aantallen. Een sluiting die met een scenario begint,
noemt het nummer van het scenario. Namen, meldingen en nummers staan niet in het log.
