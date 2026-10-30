# Gegevensvelden en persoonsgegevens

Het kaartje **Gegevensvelden** op het scherm **Geavanceerde instellingen** laat zien welke
velden van een afspraak persoonsgegevens van de burger zijn, en hoe gevoelig elk veld is. De
superuser en de rol Compliance en privacy kunnen het lezen. Niemand kan het wijzigen: de lijst
ligt vast in de software, zodat geen enkele installatie deze bescherming kan uitzetten.

## De velden

Persoonsgegevens staan alleen bij de afspraak (de boeking), nooit bij een team, een
organisatie, een berichttekst of een logregel.

- **Directe** velden identificeren de burger op zichzelf: voornaam, achternaam, e-mailadres en
  telefoonnummer.
- **Gevoelige** velden bevatten wat de burger ons heeft verteld, of geven toegang tot het
  gesprek: een notitie, de antwoorden op de eigen vragen van het team op het
  boekingsformulier, en de twee koppelingen om aan het videogesprek deel te nemen.

De antwoorden op de eigen vragen van een team gelden als persoonsgegevens, tenzij de vraag bij
het toevoegen is gemarkeerd als *geen persoonsgegevens* (zie
[het boekingsformulier](group-booking-form.md)).

## Waar persoonsgegevens worden getoond

- **De afspraakpagina** in de agenda van een team (achter *Details*) toont de antwoorden op de
  vragen van het team, voor rollen die afspraken mogen bekijken.
- **De wachtrij** toont de voornaam van de burger die aan de beurt is, en de opgepakte afspraak
  toont de koppeling om aan het gesprek deel te nemen, voor medewerkers die de wachtrij
  bedienen.
- **De agenda** en de pagina's van de planner tonen alleen de voornaam.

Nergens anders: niet op het overzicht, niet in lijsten over teams heen, niet in het auditlog of
de export daarvan, niet in een zoekresultaat en niet in een melding. Zoeken en filteren werkt
nooit op een veld met persoonsgegevens, zodat een lijst niet kan verraden dat iemand een
afspraak heeft. De rol Compliance en privacy ziet nooit persoonsgegevens.

## Bewaartermijn

Persoonsgegevens worden 24 uur na afloop of annulering van de afspraak verwijderd; zie
[Bewaartermijnen](retention.md). Van een verwijderde afspraak blijven alleen het kenmerk en
de tijden over.
