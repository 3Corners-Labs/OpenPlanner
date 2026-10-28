# Bewaartermijnen

Dit scherm laat per groep zien of persoonsgegevens van burgers op tijd worden verwijderd. U
opent het via **Bewaartermijnen** in het menu. Organisatiebeheerders, de rol Compliance en
privacy en de superuser kunnen het bekijken, elk voor hun eigen organisaties.

Het scherm toont alleen aantallen en tijden. U ziet hier nooit namen, e-mailadressen of
telefoonnummers, en ook niet welke afspraak het is.

## Wanneer gegevens worden verwijderd

- **Afspraak voorbij**: 24 uur na het einde van de afspraak.
- **Afspraak geannuleerd**: 24 uur na de annulering.
- **Afspraak opgeschort door een noodsluiting**: uiterlijk 30 dagen na het einde van de
  sluiting. Zolang de sluiting geen einde heeft, telt de afspraak nog niet mee.

Na het verwijderen blijft de afspraak bestaan zonder persoonsgegevens, voor de statistiek.

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
