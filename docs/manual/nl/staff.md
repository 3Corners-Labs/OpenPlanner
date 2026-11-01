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

## Niet herkende rechten

Stuurt de identiteitsprovider bij een login waarden mee die OpenPlanner niet herkent (een
typefout in de rol, een onbekende organisatie of groep), dan staan die op de pagina van de
medewerker onder **Niet herkende rechten**, met de reden en het moment. Het filter **Alleen met
niet-herkende rechten** vindt deze medewerkers. De juiste waarden staan op **Koppeling
identiteitsprovider**; geef de afwijking door aan het IdP-team.

## Medewerkers beheren bij de meegeleverde identiteitsprovider

Gebruikt dit platform de meegeleverde Keycloak (test- en labinstallaties), dan beheert u
medewerkers hier zelf. Bij een eigen identiteitsprovider, zoals PingFederate, ziet u deze
knoppen niet: daar wijzigt het IdP-team rechten.

- **Medewerker uitnodigen** (knop boven de lijst): vul e-mailadres, voor- en achternaam in en kies
  de eerste rol en plaats. De medewerker krijgt een e-mail met een link om een wachtwoord te
  kiezen. De link is standaard 7 dagen geldig (door de superuser in te stellen).
- **Uitgenodigd, nog niet ingelogd**: mensen die u heeft aangemaakt en die nog niet hebben
  ingelogd. U kunt de uitnodiging opnieuw sturen.
- Op de pagina van een persoon, onder **Beheer bij de identiteitsprovider**: een rol
  verwijderen, een rol met plaats toevoegen, de persoon uitschakelen of weer inschakelen, en
  een wachtwoordreset sturen. Een wijziging werkt bij de eerstvolgende login.

Wat u mag:

- Een **organisatiebeheerder** werkt alleen binnen de eigen organisaties en kent nooit meer toe
  dan de eigen rechten: de rol superuser kan alleen de superuser toekennen.
- Een persoon die ook buiten uw organisaties werkt: rollen elders blijven ongemoeid en
  uitschakelen kan alleen de superuser.
- U kunt uzelf niet wijzigen of uitschakelen.

Elke wijziging staat in de auditlog, met rolnamen en plaatsen en zonder namen of
e-mailadressen. Staat de identiteitsprovider niet bereikbaar, dan meldt het scherm dat zonder
de melding van de provider zelf.

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
  vanzelf verloopt (na 8 uur) telt niet als afmelding. Hetzelfde geldt voor een sessie die eindigt na te lang
  stilzitten (standaard 60 minuten) of omdat een nieuwere inlog haar vervangt.
