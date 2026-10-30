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

## Weergave kiezen

*Afspraken vandaag per groep* en *Wachtrij in mijn groepen* laten zich op meer manieren
tonen. Onder de titel staan de weergaven die de widget kent: lijst, staaf of taart voor de
afspraken, tabel, staaf of lijn voor de wachtrij. Een weergave kiezen laadt de pagina opnieuw
met die keuze in het adres (bijvoorbeeld `/admin/?bookings-today=bar`); de andere widgets
blijven zoals ze waren. Wat u niet kiest, is de eerste weergave. Een eigen vaste keuze per
persoon komt later (ADR 0062).

Elke grafiek is een plaatje met een tekst die alle waarden noemt, voor wie het plaatje niet
ziet. De waarden staan ook in de grafiek zelf als getal, en kleur is nooit het enige teken: bij
de taart staat een legenda met waarde en aandeel. Onder elke grafiek opent **Gegevens als
tabel** dezelfde cijfers als gewone tabel. Boven vier groepen liggen de staven; tot en met vier
staan ze rechtop. De lijn bij de wachtrij verbindt de groepen; het is dus geen verloop in de tijd.

## Standaardindeling per rol

Elke rol begint met een eigen volgorde: een medewerker ziet de wachtrij vooraan, de rol
Compliance en privacy de wijzigingen en de systeemstatus. Een eigen indeling per persoon komt
later (ADR 0062). Op een tablet staan maximaal twee widgets naast elkaar, op een telefoon één.

## Uw profielmenu

Rechtsboven opent uw naam of uw initialen het profielmenu. Het staat op elke pagina van het
beheer en werkt zonder JavaScript. Van boven naar beneden:

- **Uw rollen** en waar ze vandaan komen. De rol komt uit uw organisatie-account en is hier niet
  te wijzigen; **Mijn rechten bekijken** opent de pagina met wat uw rol toestaat en waar.
- **Weergave**: Systeem (volgt uw apparaat), Licht of Donker. De keuze geldt voor deze sessie en
  blijft ook na opnieuw inloggen; uitloggen zet haar terug op Systeem.
- **Kies een taal**: Nederlands of English. De keuze onthoudt de browser.
- **Meldingen**: of de balk bij storingen getoond wordt en welke meldingen als pop-up verschijnen
  (alle, waarschuwingen en storingen, alleen storingen, geen). **Opslaan** bewaart de keuze voor
  deze sessie. De meldingen zelf volgen in een volgende versie (ADR 0061); de keuze staat dan al klaar.
- **Uitloggen**.

Op een telefoon blijven in de balk alleen de pictogrammen en uw initialen staan; uw naam staat in
het menu.

## De balk bovenin

Rechts van de naam van het beheer staan, van links naar rechts, kleine snelkoppelingen. U ziet
alleen wat uw rol mag openen:

- **Status** opent *Geavanceerde instellingen* met de systeemstatus (superuser en compliance).
- **Taken** springt naar de widget *Lopende taken* op dit overzicht, met het aantal lopende en
  mislukte taken ernaast. Een getal groter dan nul betekent dat er iets draait of vastzit. De
  snelkoppeling staat er alleen voor wie die widget op het overzicht heeft.
- **Meldingen** opent het auditlog vanaf het niveau waarschuwing. Zodra het meldingenmodel er is
  (ADR 0061) opent de snelkoppeling de lijst met meldingen; een getal volgt dan ook.
- **Handleiding** opent het onderwerp van de pagina waar u op staat.

Op een telefoon blijven de pictogrammen, het getal en uw initialen staan; de woorden staan
voor schermlezers in de balk. Het getal staat ook in de naam van de snelkoppeling, bijvoorbeeld
*Taken, 2 lopend of mislukt*. Een snelkoppeling zonder bron voor een getal toont er geen.

## Werkgebied kiezen

Links in de balk staat een knop met een filter-pictogram, op een breed scherm met de keuze erbij
(*Werkgebied: Alle organisaties › Alle groepen*). Daarin kiest u een organisatie, een groep of
beide, en **Tonen** beperkt het overzicht tot die keuze: de groepen onderaan, *Afspraken vandaag
per groep*, *Wachtrij in mijn groepen* en *Recente wijzigingen van de configuratie*. *Taken*,
*Systeemstatus* en *Mijn rol en bereik* gelden voor het hele platform en veranderen niet.
**Alles tonen** haalt de keuze weg.

Het filter laat alleen zien wat uw rol al mag zien. U kiest uit de organisaties en groepen
binnen uw bereik; een andere waarde in het adres wordt genegeerd. Het is een gewoon formulier
dat niets wijzigt. Boven de keuzelijsten staan de gekozen organisatie en groep als links naar hun
pagina's (de organisatiepagina alleen voor wie organisaties beheert). De keuze geldt voor deze
sessie: u ziet haar terug als u later naar het overzicht gaat, en na opnieuw inloggen staat het
filter weer op alles. Is een keuze actief, dan heeft de knop een gekleurde achtergrond, ook als er
op een kleiner scherm alleen het pictogram past. Op dit moment werkt het filter op het overzicht;
de andere lijsten hebben hun eigen filters.
