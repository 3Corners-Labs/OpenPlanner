# Overzicht

Het overzicht is de startpagina van het beheerportaal. Het toont in één oogopslag wat nu
aandacht vraagt, als een raster van **widgets**. Elke widget heeft een eigen recht: u ziet
alleen de widgets die bij uw rol horen, en nooit meer dan uw rol mag zien. Onder het raster
staan de groepen waar u rechten op heeft, met hun tekorten aan medewerkers.

De pagina werkt zonder JavaScript. Wat u ziet is de stand van het moment van laden; met
**Vernieuwen** laadt u de pagina opnieuw. Een latere versie ververst de widgets vanzelf.

Het overzicht toont nooit persoonsgegevens van burgers: alleen aantallen, groepsnamen en
tijden.

## Widgets

- **Mijn rol en bereik**: uw rollen en de eerste rechten met het bereik waar ze gelden.
  **Alles bekijken** opent de pagina *Mijn rechten*.
- **Afspraken vandaag per groep**: het aantal bevestigde afspraken van vandaag per groep in uw
  bereik, de drukste groep bovenaan. De groepsnaam opent de agenda van die groep.
- **Recente configuratiewijzigingen**: de laatste geslaagde wijzigingen door beheerders binnen
  uw bereik, met wie, wanneer en waar. Een regel opent de hele keten in het auditlog;
  **Alles bekijken** opent het auditlog zelf.

- **Wachtrij in mijn groepen**: per groep met een eigen wachtrij in uw bereik hoeveel burgers
  er wachten, hoe lang de langste wacht al duurt en hoeveel medewerkers die de wachtrij mogen
  bedienen nu zijn aangemeld. De groepsnaam opent de wachtrij. Alleen aantallen en minuten,
  nooit namen. Kleuren bij een te lange wachttijd volgen met de wachttijdgrenzen.
- **Lopende taken**: de achtergrondtaken die nu bezig zijn en de taken die na de laatste
  poging zijn mislukt, met soort, status en tijdstip. Nooit de inhoud van een taak of de
  afspraak waar ze bij hoort. Staat hier iets als *Mislukt*, dan loopt er iets vast: meld het
  aan de beheerder van de installatie.
- **Systeemstatus**: de draaiende versie en of er een update is, of onderhoud aan staat, of
  het achtergrondproces recent een hartslag gaf, of de diagnosemodus aan staat en in welke
  omgeving de installatie draait. De regels openen de pagina's die er meer over weten, voor
  zover uw rol die mag openen.

De widget *Meldingen* volgt in een volgende versie.

## Standaardindeling per rol

Elke rol begint met een eigen volgorde: een medewerker ziet de wachtrij vooraan, de rol
Compliance en privacy de wijzigingen en de systeemstatus. Een eigen indeling per persoon komt
later (ADR 0062). Op een tablet staan maximaal twee widgets naast elkaar, op een telefoon één.
