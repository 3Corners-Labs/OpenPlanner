# Medewerkers

Dit scherm laat zien wie het beheerportaal gebruikt. U opent het via **Medewerkers** in het
menu. U ziet per persoon de rollen, de organisaties en groepen waarvoor die rollen gelden, en
wanneer de persoon voor het laatst heeft ingelogd, actief was en zich heeft afgemeld.

Wie u ziet, hangt af van uw eigen rol:

- **Organisatiebeheerder** en **Compliance en privacy**: medewerkers van uw eigen
  organisaties en hun groepen.
- **Groepsbeheerder**: medewerkers van uw eigen groepen.
- **Superuser**: iedereen.

Superusers ziet alleen de superuser. Werkt iemand ook voor een andere organisatie, dan ziet u
dat niet: u ziet alleen de organisaties en groepen die u zelf beheert.

## Waar de gegevens vandaan komen

Rollen, organisaties en groepen komen van de identiteitsprovider (de inlogdienst van uw
organisatie). OpenPlanner neemt ze over bij elke login. Wijzigt het IdP-team iemands rol, dan
ziet u dat hier pas na de volgende login van die persoon. Rechten toekennen of intrekken doet
u daarom bij de identiteitsprovider, niet hier.

Elke wijziging die OpenPlanner bij een login ziet, staat in de auditlog als **Rollen of
lidmaatschappen gewijzigd**, met wat erbij kwam en wat eraf ging.

## Filters

- **Organisatie**, **Groep** en **Rol**: alleen medewerkers die daarbij horen.
- **Alleen wie lang niet heeft ingelogd**: medewerkers zonder login in de ingestelde periode
  (standaard 90 dagen). Gebruik dit voor een periodieke toegangscontrole: heeft deze persoon
  de toegang nog nodig?

## De pagina van een medewerker

Klik op een naam. U ziet de toegang, de tijden en de recente activiteit uit de auditlog:
logins, afmeldingen en wijzigingen, binnen uw eigen organisaties of groepen.

## Tijden

- **Laatst ingelogd**: de laatste keer dat de persoon via de identiteitsprovider inlogde.
- **Laatst actief**: de laatste keer dat de persoon een pagina opende. Dit wordt hooguit
  eens per vijf minuten bijgewerkt.
- **Laatst afgemeld**: de laatste keer dat de persoon op **Afmelden** klikte. Een sessie die
  vanzelf verloopt (na 8 uur) telt niet als afmelding.
