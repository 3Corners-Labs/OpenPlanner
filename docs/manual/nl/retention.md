# Bewaartermijnen

Dit scherm laat per groep zien of persoonsgegevens van deelnemers op tijd worden verwijderd. U
opent het via **Bewaartermijnen** in het menu. Organisatiebeheerders, de rol Compliance en
privacy en de superuser kunnen het bekijken, elk voor hun eigen organisaties.

Het scherm toont alleen aantallen en tijden. U ziet hier nooit namen, e-mailadressen of
telefoonnummers, en ook niet welke afspraak het is.

## Wanneer gegevens worden verwijderd

- **Afspraak voorbij**: 24 uur na het einde van de afspraak.
- **Afspraak geannuleerd**: 24 uur na de annulering.
- **Afspraak opgeschort door een noodsluiting**: 30 dagen na het einde van de sluiting, en
  uiterlijk het maximum van de organisatie na de oorspronkelijke afspraak (standaard 90 dagen),
  ook zolang de sluiting geen einde heeft.

Bij het verwijderen gaan de persoonsvelden van de boeking weg, samen met de koppelingen naar de
vergadering en het werkitem bij de aanbieders en de redentekst op de tijdlijn. De afspraak zelf
blijft bestaan met zijn referentie en tijden; samen met gegevens elders kunnen die nog naar een persoon wijzen.

De afspraak zelf verdwijnt later ook: de beheerder van het platform stelt in hoeveel dagen na het
verwijderen van de gegevens dat gebeurt (standaard 730). Van de afspraken blijft dan alleen een telling per
groep, dag en eindstatus over, zonder persoonsgegevens of referentie. De cijfers op dit scherm
veranderen daardoor niet; ook **Laatst verwijderd** blijft kloppen.

## Wat de kolommen betekenen

- **Met persoonsgegevens**: afspraken waarvan de gegevens er nog zijn. Dat is normaal voor
  afspraken die nog komen of net voorbij zijn.
- **Over de bewaartermijn**: afspraken waarvan de gegevens een uur na het moment van
  verwijderen nog niet weg zijn. Dit hoort 0 te zijn.
- **Mislukte verwijderingen**: verwijderingen die na alle pogingen zijn mislukt. Dit hoort 0
  te zijn.
- **Laatst verwijderd**: wanneer voor het laatst gegevens van een afspraak in de groep zijn
  verwijderd.
- **Status**: **In orde** of **Actie nodig**. De status staat er altijd in woorden bij.

## Als er actie nodig is

Meld het bij de beheerder van de installatie. In de auditlog staat bij de
systeemgebeurtenis **Persoonsgegevens verwijderen** de foutcode. Een achterstand ontstaat
meestal doordat de achtergrondverwerking (de worker) niet draait.
