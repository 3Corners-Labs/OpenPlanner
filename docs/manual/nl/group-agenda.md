# Agenda van de groep

De agenda laat per tijdslot zien hoeveel medewerkers er zijn en hoeveel afspraken er staan.
U opent hem via het tabblad **Agenda** van een groep. Iedereen die de afspraken van de groep
mag zien, kan de agenda openen: groepsbeheerders, organisatiebeheerders en de superuser, en
een eigen rol met het recht om afspraken te bekijken.

Het aantal medewerkers komt uit de bezetting van de groep: de vaste blokken, de
uitzonderingen, de feestdagen en een noodsluiting. Verandert u de bezetting, dan ziet u het
meteen in de agenda.

## Dagoverzicht

Elk tijdslot van de dag staat in een eigen blok, met de tijd, het aantal medewerkers en het
aantal bevestigde afspraken. Daaronder staan de afspraken met de voornaam van de deelnemer,
de stand van de videomeeting en de contactcentertaak. Het e-mailadres en het telefoonnummer
ziet u alleen op de detailpagina van een afspraak (**Details**).

- **Gesloten**: een noodsluiting valt over dit tijdslot.
- **Buiten de bezetting**: er staat hier alleen een geannuleerde of afgelopen afspraak; de
  bezetting heeft op dit moment geen tijdslot meer.
- **Direct gesprekken**: gesprekken die zonder afspraak zijn gestart, staan onderaan.

Met **Vorige dag**, **Vandaag** en **Volgende dag** bladert u. **Weekoverzicht** opent de
week van de gekozen dag.

### De stand van de meetings blijft actueel

De kolommen **Meeting**, **Medewerker** en **Deelnemers** verversen zichzelf, zonder dat de
pagina opnieuw laadt. Een schermlezer hoort daarbij één korte zin ("Status bijgewerkt: 1
gesprekken bezig, 0 niet opgepakt"); de focus verspringt alleen als die in de tabel stond.
Vandaag en in de toekomst gebeurt dat elke 30 seconden; een dag in het verleden verandert
niet meer en ververst dus niet. De superuser stelt de 30 seconden in bij de
platforminstellingen (**Meetingstatus in de agenda verversen**, 10 tot 300 seconden).

De stand komt in de eerste plaats van Webex zelf, dat een gebeurtenis stuurt zodra een meeting
begint of eindigt of iemand erbij komt of weggaat. Komt zo'n gebeurtenis niet aan, dan vraagt
OpenPlanner Webex om de stand zodra die ouder is dan de verversingstijd, en alleen voor
afspraken die een lopende meeting kunnen hebben: bevestigd, met een meeting, vanaf 15 minuten
voor het begin tot 30 minuten na het einde. Een gemiste gebeurtenis ziet u dus binnen een halve
minuut gecorrigeerd. In **Verloop** staat zo'n gebeurtenis met de toevoeging *polled*.

Werkt Webex niet, dan blijft de agenda zoals hij was. Op de detailpagina van de afspraak staat
bij **Verloop** een gebeurtenis *Koppeling mislukt* met de code (bijvoorbeeld
`polled:rejected_403`) en, onder *Antwoord van Webex*, wat Webex zelf als reden gaf. Dat
staat er één keer per soort fout, niet bij elke verversing.

Werkt uw browser zonder scripts of staat JavaScript uit, dan staat de agenda er zoals hij
laadde en ververst de link **Vernieuwen** hem.

## Een afspraak uitzoeken

Op de detailpagina van een afspraak (**Details**) staat alles wat nodig is als een
videogesprek of contactcentertaak niet goed gaat:

- **Verloop**: wat er met de meeting en de taak gebeurde. Achter elke gebeurtenis staat de
  versie van OpenPlanner die haar schreef. Op een testomgeving die vaak bijwerkt, ziet u zo of
  een fout van een oudere versie komt. *Versie onbekend* betekent: geschreven voordat
  OpenPlanner de versie bewaarde.
- **Achtergrondtaken**: elke taak van de afspraak (videomeeting, contactcentertaak, berichten,
  persoonsgegevens verwijderen) met het geplande tijdstip, de stand, het aantal pogingen en de
  foutcode. Is de videomeeting of de contactcentertaak mislukt, dan staat er **Opnieuw
  proberen**. Die knop ziet u als u afspraken van de groep mag wijzigen. De worker voert de
  taak binnen een minuut uit; vernieuw de pagina voor de uitkomst. Een taak die niets hoefde te
  doen (de meeting bestond al, of de groep gebruikt de eigen wachtrij) staat in het logboek als
  *Overgeslagen* met de reden, niet als geslaagd.
- **Verzoeken aan Webex en Webex Contact Center**: elke oproep die OpenPlanner voor de afspraak
  deed, met het antwoord (statuscode), de duur, de *tracking-id* en de versie. De tracking-id
  geeft u door aan Cisco-support als Webex de fout moet uitzoeken. Er staan geen
  persoonsgegevens in. Deze verzoeken worden standaard 30 dagen bewaard.

## Weekoverzicht

Een tabel met de tijden in de rijen en de dagen van maandag tot en met zondag in de kolommen.
Elke cel noemt het aantal medewerkers en afspraken en opent dat tijdslot in het dagoverzicht.
Een streepje betekent dat er op die dag geen tijdslot is. Het weekoverzicht toont alleen
aantallen, geen namen.

## Tekorten

Een tijdslot met meer bevestigde afspraken dan medewerkers heeft een **tekort**. Dat gebeurt
als u de bezetting verlaagt nadat er al is geboekt: afspraken blijven altijd staan. Een
tekort staat er in woorden bij (**Tekort: 1**) met een waarschuwingsteken, nooit alleen als
kleur.

Het overzicht van de groep, de pagina's **Bezetting**, **Afwijkende bezetting** en **Feestdagen**
en het dashboard melden hoeveel tijdsloten in de komende 14 dagen een tekort hebben, met een
link naar het eerste. Zo ziet u direct na een wijziging van de bezetting of die een tekort
geeft.

Een tekort lost u op door de bezetting weer te verhogen, of door afspraken te wijzigen (hieronder).

## Afspraken wijzigen

Met het recht **Boekingen wijzigen** (groepsbeheerders, organisatiebeheerders en een eigen
rol Planner) staan in het dagoverzicht bij elke afspraak die nog kan worden gewijzigd de links
**Verplaatsen** en **Annuleren**. De burger krijgt van elke wijziging altijd bericht: per
e-mail, en per sms als de burger een telefoonnummer opgaf. U logt daarvoor soms opnieuw in.

Een afspraak kan niet meer worden gewijzigd als die binnen de tijd van **Wijzigen door een
planner** begint (instelling van de groep, standaard 60 minuten), als die al naar het
contactcenter is gegaan, of als die al is geannuleerd. Bel de burger dan.

### Verplaatsen

Kies een vrij tijdslot van hetzelfde team in de komende 28 dagen. De burger krijgt een bericht
met de nieuwe tijd en een nieuwe link om zelf nog te wijzigen. Heeft het gekozen tijdslot
daarna meer afspraken dan medewerkers, dan vraagt OpenPlanner u dat eerst te bevestigen met
het vinkje **Ik accepteer dat het nieuwe tijdslot dan meer afspraken dan medewerkers heeft**.

### Annuleren

Kies een van de vaste redenen: **Te weinig medewerkers**, **Technische storing** of
**Onvoorziene omstandigheden**. De burger leest de bijbehorende tekst in het bericht. U kunt
zelf geen tekst typen; de organisatie past de woorden aan bij **Berichtteksten**, onder
**Redenen bij wijzigingen door een planner**.

### Om een nieuwe tijd vragen

Met **Om nieuwe tijd vragen** bij een afspraak kiest de burger zelf een nieuwe tijd. De plek in
het tijdslot komt meteen vrij, dus het tekort verdwijnt direct. De burger krijgt een bericht
met de reden en een link om een nieuwe tijd te kiezen, zonder de gegevens opnieuw in te
vullen. Drie dagen voor het verlopen volgt één herinnering. Kiest de burger binnen 14 dagen na
de oorspronkelijke tijd niets, dan vervalt de afspraak; de burger krijgt daarvan een kort
bericht en de persoonsgegevens worden een dag later verwijderd.

Bij een tijdslot met een tekort staat ook **Burgers om een nieuwe tijd vragen**. U ziet dan
eerst welke afspraken het betreft: de laatst gemaakte, zoveel als het tekort. Na uw
bevestiging krijgen precies die burgers de vraag. Is het tijdslot intussen veranderd, dan
toont OpenPlanner de nieuwe lijst en vraagt het u opnieuw te bevestigen.

De afspraken die op een nieuwe tijd wachten, staan in de lijst **Wacht op nieuwe tijd** bij de
agenda, met de oorspronkelijke tijd, de reden en de datum waarop de vraag vervalt. Ook daar
kunt u een afspraak zelf verplaatsen of annuleren.

### Tekort accepteren

Helpt er bijvoorbeeld een collega van een ander team, dan kiest u bij het tijdslot **Tekort
accepteren**. Het tekort telt dan niet meer mee in de meldingen. Veranderen daarna de
bezetting of de afspraken van dat tijdslot, dan verschijnt het tekort weer.

Elke wijziging staat in de auditlog met wie, welk team, het afspraaknummer en de reden. Doet
één persoon in een uur veel wijzigingen, dan meldt een systeemgebeurtenis dat aan de
beheerders.
