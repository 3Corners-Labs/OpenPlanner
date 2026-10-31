# Teksten voor deelnemers

Een organisatie kan de teksten aanpassen die deelnemers zien op de pagina's van haar groepen en in
de mails en sms'jes. U opent het scherm via **Organisaties → organisatie → Overzicht → Teksten**: kies de
organisatie en dan **Teksten aanpassen**. Organisatiebeheerders passen de teksten van hun eigen
organisaties aan; de superuser die van elke organisatie.

Een tekst die u hier aanpast geldt voor alle groepen van de organisatie. De volgorde is:

1. de eigen tekst van een groep, ingesteld bij [de berichtteksten van de groep](group-settings.md);
2. de tekst van de organisatie, ingesteld op dit scherm;
3. de standaardtekst van OpenPlanner.

## Wat u kunt aanpassen

- **Berichten**: het onderwerp en de inleiding van elke mail (bevestiging, wijziging,
  herinnering, annulering, verzoek om opnieuw te boeken), de sms-teksten, de redenen die een
  planner kan kiezen en de afsluitende regel. Dit zijn dezelfde teksten als bij een groep.
- **Pagina's voor deelnemers**: de inleiding op de pagina van de groep, de kaart voor een direct
  gesprek, de uitleg na het boeken en op de pagina om een afspraak te bevestigen.

Andere teksten, zoals knoppen, formulierlabels en foutmeldingen, kunt u niet aanpassen.

## Hoe u de medewerker noemt

OpenPlanner noemt de persoon die het videogesprek aanneemt een *medewerker* ("agent" in de
Engelse teksten). Uw organisatie kan voor haar eigen groepen een ander woord kiezen, zoals
"behandelaar" of "adviseur". Onderaan dit scherm, in de kaart *Hoe u de medewerker noemt*, vult
u dat woord in het enkelvoud en in het meervoud in, per taal. Beide wijzigen samen: het
formulier weigert een enkelvoud zonder meervoud.

Het woord staat daarna overal waar de standaardteksten over deze persoon spreken: in de mails
en sms'jes, op de pagina's voor deelnemers en op de schermen van uw groepen (openingstijden,
wachtrij, agenda). Het woord is alleen een label. Het wijzigt geen rechten, en het
auditlogboek en de rechtenmatrix houden hun eigen namen.

Een groep kan een eigen woord gebruiken, bijvoorbeeld als één team "adviseurs" zegt: de
groepsbeheerder stelt dat in bij [de berichtteksten van de groep](group-settings.md); het gaat
voor het woord van de organisatie. In een eigen tekst schrijft u `{agent}` of `{agents}` voor
het woord, en `{Agent}` of `{Agents}` waar het woord een zin of een label begint. Schrijf
"uw {agent}" of "een {agent}" en niet "de {agent}": een woord kan een *het*-woord zijn.

De pagina's van deze handleiding gebruiken de standaardwoorden. Uw eigen woord vervangt ze op het
scherm.

## Hoe u de beheerders noemt

Een organisatie kan ook haar *organisatiebeheerder* en haar *groepsbeheerder* een eigen naam
geven, bijvoorbeeld "Hoofd planning" en "Teamleider". U vult de namen in bij de kaart *Hoe u de
beheerders noemt*, per taal, op dit scherm. Een naam is een label op het scherm: hij wijzigt geen
rechten. De superuser en de auditor kunt u niet hernoemen, en een eigen rol houdt de naam die de
superuser gaf.

Overal waar een naam naast rechten staat, in de rechtenmatrix en bij *Mijn rechten*, blijft de
standaardnaam tussen haakjes zichtbaar, bijvoorbeeld "Hoofd planning (Organisatiebeheerder)".
Het auditlogboek en het rechtendocument houden de standaardnamen. Een naam mag niet de naam van
een andere rol zijn, met of zonder hoofdletters: "Groepsbeheerder" wordt geweigerd als naam van de
organisatiebeheerder.

## Een tekst wijzigen

Kies eerst de taal. Een organisatie kan voor elke taal eigen teksten hebben. Pas de tekst aan en
kies **Opslaan**; de wijziging geldt binnen ongeveer vijf seconden voor nieuwe pagina's en
nieuwe berichten. Verstuurde mails veranderen niet. Maak een veld leeg, of zet er weer de
standaardtekst in, om de standaardtekst terug te krijgen.

Onder elk veld staat welke plaatshouders u kunt gebruiken, zoals `{date}` of `{deadline}`. Op
die plek zet OpenPlanner de datum of de uiterste tijd in. U mag een plaatshouder weglaten, maar
geen andere gebruiken. Een tekst heeft een maximale lengte; een onderwerp of titel heeft geen
regeleinden.

Teksten zijn gewone tekst. Opmaak zoals `<b>` of code wordt niet uitgevoerd maar letterlijk
getoond. Zet geen persoonsgegevens in een tekst: alle deelnemers zien dezelfde tekst.

Voor een wijziging moet u kort geleden zijn ingelogd. Elke wijziging staat in het auditlog met
de taal en de namen van de aangepaste teksten, niet met de teksten zelf.
