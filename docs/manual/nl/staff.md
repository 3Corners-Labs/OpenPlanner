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

Ziet u een gele melding dat de meegeleverde identiteitsprovider **niet volledig gekoppeld** is,
dan draait Keycloak wel maar mist de server een instelling; de melding noemt welke. Dat lost
de serverbeheerder op (runbook, *Bundled identity provider*); tot dan ontbreken de knoppen. De
superuser ziet de stand ook op **Systeemstatus**, onder *Medewerkerbeheer*.

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

## Sessies van een medewerker beëindigen

Op de pagina van een medewerker staat de knop **Alle sessies beëindigen**. Organisatiebeheerders
en de superuser zien hem (permissie `staff.manage`). Hij werkt voor elke medewerker die u
in het overzicht ziet; voor iemand daarbuiten wordt hij geweigerd en staat de poging in het
auditlog.

Een sessie hoort bij de persoon, niet bij een organisatie. Beëindigt u alle sessies van iemand
die voor twee organisaties werkt, dan eindigt dus ook wat die persoon voor de andere
organisatie gebruikt. Dat is bedoeld: beëindigen beschermt en geeft niemand meer rechten. De
medewerker moet opnieuw inloggen. Gebruik het bijvoorbeeld als iemand is uitgezet bij de
identiteitsprovider: anders blijft de sessie werken tot ze stilvalt (standaard 60 minuten). Het
auditlog heeft een gebeurtenis met u, de medewerker en het aantal sessies. Iedereen kan zijn
eigen sessies zien en beëindigen op [Mijn sessies](my-sessions.md).

## Een medewerker schorsen, heractiveren en wissen

Onder **Toegang** op de pagina van een medewerker kunt u de persoon **schorsen** (permissie
`staff.manage`, organisatiebeheerders en de superuser). Schorsen blokkeert het beheerportaal
voor die persoon, wat de identiteitsprovider ook nog over de rechten zegt, en beëindigt in één
keer alle sessies. Een login wordt geweigerd met de melding *uw toegang is geschorst*; de poging
staat in het auditlog. **Schorsing opheffen** laat de persoon weer inloggen; de rechten komen
dan opnieuw uit de identiteitsprovider. U kunt uzelf niet schorsen en alleen de superuser kan een
superuser schorsen. Elke stap is een auditgebeurtenis (`staff.suspended`, `staff.reinstated`)
met de beheerder en de persoon, zonder naam.

De superuser kan de gegevens van een geschorste persoon ook **wissen** (permissie
`staff.erase`): naam, rollen en lidmaatschappen verdwijnen en de pagina toont daarna *Gewiste
medewerker*. Het auditlog blijft naar die gewiste persoon verwijzen. Dit kan niet ongedaan
worden gemaakt. Logt de persoon daarna opnieuw in, dan begint een nieuw record.

Wissen gebeurt ook vanzelf. Wie 365 dagen niet heeft ingelogd (de superuser stelt dit in bij
*Geavanceerde instellingen*, 90 tot 1825 dagen: *Medewerkergegevens wissen na*) wordt door de
worker eenmaal per dag gewist, als systeemgebeurtenis `staff.erased`. Wie vóór dat moment
terugkomt, blijft staan.

## Tweede factor (MFA)

Medewerkers loggen in met een tweede factor naast het wachtwoord: een code uit een
authenticator-app of een beveiligingssleutel. OpenPlanner richt dit niet in en bewaart het
niet; dat doet de identiteitsprovider (ADR 0100). Bij het inloggen vraagt OpenPlanner erom en
controleert dat de provider zegt dat het gebruikt is. Is dat niet zo, dan wordt het inloggen
geweigerd met de melding *uw account laat geen tweede factor zien*. Stel meervoudige
authenticatie in bij uw identiteitsprovider (bij de meegeleverde Keycloak vraagt de eerste
login om een authenticator-app) en log opnieuw in. Blijft het mislukken, vraag dan het
IdP-team; de weigering staat in het auditlog als `mfa_required`, zonder persoonsgegevens.
