# Onderwerpen en routing van een team

Een team is drie dingen tegelijk: de agenda waarin een deelnemer boekt, de mensen die het gesprek
voeren en de wachtrij waar het werk heen gaat. Op dit scherm geeft u het team de laag die daarnaast
ontbreekt: het **onderwerp** dat de deelnemer kiest en een paar vaste **routingvragen**, zoals de
taal van het gesprek. U kunt die keuzes zelf gebruiken (in de agenda en de eigen wachtrij, en om
alleen de tijden te tonen waarop er iemand voor het onderwerp is) en ze als codes doorgeven aan het
contactcenter, zodat de flow daar de juiste medewerker kiest.

U opent het scherm via **team → Instellingen → Onderwerpen en routing**. Een organisatiebeheerder
of superuser kan het wijzigen.

Niets op dit scherm is een persoonsgegeven: een onderwerp en een antwoord op een routingvraag zijn
een keuze uit een vaste lijst die uw organisatie of OpenPlanner schreef. Vrije tekst en de eigen
velden van het [boekingsformulier](group-booking-form.md) gaan nooit naar het contactcenter.

## Onderwerpen

Met één of meer onderwerpen vraagt het formulier de deelnemer als eerste vraag waarover de afspraak
gaat. Zonder onderwerpen is het formulier zoals altijd. De deelnemer ziet de onderwerpen ook al op
de pagina van het team, boven de kalender: wie daar een onderwerp kiest, ziet alleen de tijden van
blokken die het aanbieden, en het formulier heeft het onderwerp dan al gekozen. Een website die de
widget insluit, kan het onderwerp vooraf kiezen met het attribuut `subject`.

Een team heeft hoogstens het aantal onderwerpen dat de superuser instelt (*Onderwerpen per groep*,
standaard 20). Met **Omhoog** en **Omlaag** zet u ze in de volgorde van het formulier. Een
onderwerp dat een blok nog aanbiedt, kunt u niet verwijderen; haal het eerst bij het blok weg.
Een verwijderd onderwerp blijft bij de afspraken die het kozen staan, als code.

<!-- field: label_nl -->
### Naam (Nederlands)

Wat de deelnemer leest, kort en in gewone taal, bijvoorbeeld *Verhuizing doorgeven*. Hoogstens 80
tekens.

<!-- field: label_en -->
### Naam (Engels)

Dezelfde naam in het Engels; de deelnemer die de pagina in het Engels opent, leest deze.

<!-- field: code -->
### Code

De vaste naam van het onderwerp: kleine letters, cijfers en streepjes, hoogstens 40 tekens,
bijvoorbeeld `verhuizing`. Na opslaan ligt hij vast. Boekingen bewaren de code, rapportages tellen
erop, en het contactcenter krijgt de waarde die u er hieronder aan koppelt.

## Onderwerpen per blok

Bij een blok op het tabblad **Bezetting** en bij een uitzondering kiest u welke onderwerpen het
blok aanbiedt. Niets aangevinkt: het blok biedt alle onderwerpen, zoals voorheen. Zo kunt u
bijvoorbeeld 's middags alleen de medewerkers inzetten die verhuizingen doen. De capaciteit van het
blok geldt voor alle onderwerpen samen.

## Routingvragen

OpenPlanner heeft een vaste lijst van vragen waarvan het antwoord een code is:

- **Voorkeurstaal**: de talen waarin OpenPlanner zijn pagina's toont (Nederlands, Engels).
- **Manier van contact**: met beeld (video) of telefonisch.

Vink aan wat het formulier moet vragen. Een organisatie kan geen eigen routingvraag maken; een
nieuwe vraag komt er alleen bij een besluit (ADR), omdat steeds moet vaststaan dat het antwoord
geen persoonsgegeven is.

## Doorgeven aan het contactcenter

Per onderwerp en per routingvraag kiest u de naam van de variabele die de flow in het contactcenter
leest, en de waarde die OpenPlanner per keuze stuurt. Bij Webex Contact Center gaat de variabele mee
in `flowSettings` van de taak, naast `openplannerGroup`; maak in de flow een variabele met precies
deze naam. Wat leeg blijft, wordt niet verstuurd: een onderwerp zonder waarde, een vraag zonder
variabele.

<!-- field: variable -->
### Naam van de variabele

Letters, cijfers en `_`, beginnend met een letter, hoogstens 64 tekens, bijvoorbeeld `onderwerp`.
De naam `openplannerGroup` is van OpenPlanner zelf. Leeg maken en opslaan stopt het versturen.

### Verstuurde waarde

Een code van letters, cijfers, `.`, `:`, `-` of `_`, hoogstens 64 tekens, bijvoorbeeld `MOVE`. Vaak
is het de code zelf, of de naam van een skill of wachtrij in het contactcenter.
