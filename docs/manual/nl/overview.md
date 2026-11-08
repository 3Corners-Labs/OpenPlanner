# Overzicht

Het overzicht is de startpagina van het beheerportaal. Het toont in één oogopslag wat nu
aandacht vraagt, als een raster van **widgets**. Elke widget heeft een eigen recht: u ziet
alleen de widgets die bij uw rol horen, en nooit meer dan uw rol mag zien. Onder het raster
staan de groepen waar u rechten op heeft, met hun tekorten aan medewerkers.

De widgets verversen zichzelf: elke paar seconden vraagt de pagina of er iets veranderd is en
vervangt alleen de widget waarin iets veranderde. De focus blijft waar hij is. Wat er verandert
in *Meldingen* wordt voorgelezen voor wie een schermlezer gebruikt. Zonder JavaScript, of als de
superuser het verversen heeft uitgezet (*Overzicht ververst zichzelf*), ziet u de stand van het
moment van laden en laadt **Vernieuwen** de pagina opnieuw. Hoe vaak het overzicht ververst, stelt
de superuser in (*Overzicht verversen*, 5 tot 60 seconden).

Het overzicht toont nooit persoonsgegevens van deelnemers: alleen aantallen, groepsnamen en
tijden.

## Widgets

- **Meldingen**: wat nu aandacht vraagt binnen uw bereik, het dringendste bovenaan. Elke melding
  noemt haar niveau in woorden (*Storing*, *Waarschuwing*), waar ze over gaat, wat er mis is en
  sinds wanneer. Een melding blijft staan tot het probleem is opgelost; dan verdwijnt ze vanzelf.
  De titel opent waar u verder kunt: de hele keten in het auditlog, de wachtrij van de groep of de
  systeemstatus. Met **Alle**, **Waarschuwingen en storingen** en **Alleen storingen** filtert u op
  niveau; als staaf of taart ziet u het aantal per niveau. Meldingen komen van de
  opslagcontrole, het certificaat, het publieke adres, een logbestemming, het verwijderen van
  gegevens na hun bewaartermijn, de automatische controle van een koppeling, een te lange
  wachttijd in een eigen wachtrij en een worker die geen hartslag meer geeft. U ziet alleen
  meldingen waar uw rol bij mag: platformmeldingen alleen met recht op de systeemstatus, een
  wachtrijmelding alleen wie die wachtrij bedient.
- **Mijn rol en bereik**: uw rollen en de eerste rechten met het bereik waar ze gelden.
  **Alles bekijken** opent de pagina *Mijn rechten*.
- **Afspraken vandaag per groep**: het aantal bevestigde afspraken van vandaag per groep in uw
  bereik, de drukste groep bovenaan. De groepsnaam opent de agenda van die groep.
- **Recente configuratiewijzigingen**: de laatste geslaagde wijzigingen door beheerders binnen
  uw bereik, met wie, wanneer en waar. Een regel opent de hele keten in het auditlog;
  **Alles bekijken** opent het auditlog zelf.

- **Wachtrij in mijn groepen**: per groep met een eigen wachtrij in uw bereik hoeveel deelnemers
  er wachten, hoe lang de langste wacht al duurt en hoeveel medewerkers die de wachtrij mogen
  bedienen nu zijn aangemeld. Een deelnemer met een afspraak telt mee zodra die in de lobby
  staat; een directe oproep telt vanaf de aanvraag, ook als de link nog niet is geopend. De groepsnaam opent de wachtrij. Alleen aantallen en minuten,
  nooit namen. Onder de langste wachttijd staat of die binnen het doel blijft, erboven zit, of boven
de grens waarop een melding komt (*Doel voor de wachttijd* en *Melding bij wachttijd boven*,
instellingen van de organisatie; standaard 5 en 8 minuten). De kleur herhaalt alleen wat er staat.
Gaat de langste wachttijd over de grens, dan komt er één melding; die verdwijnt als de wachttijd
weer onder de grens is.
- **Lopende taken**: de achtergrondtaken die nu bezig zijn en de taken die na de laatste
  poging zijn mislukt, met soort, status en tijdstip. Nooit de inhoud van een taak of de
  afspraak waar ze bij hoort. Staat hier iets als *Mislukt*, dan loopt er iets vast: meld het
  aan de beheerder van de installatie.
- **Systeemstatus**: de draaiende versie en of er een update is, of onderhoud aan staat, of
  het achtergrondproces recent een hartslag gaf, hoeveel schijfruimte er vrij is en hoe groot de
  database is, of de diagnosemodus aan staat en in welke omgeving de installatie draait. De regels openen de pagina's die er meer over weten, voor
  zover uw rol die mag openen.

## De balk bij storingen

Een **storing** in uw bereik staat als balk bovenaan elke pagina van het beheer, tot ze is
opgelost: de schijf is bijna vol, het certificaat is verlopen of niet vertrouwd, of de worker geeft
geen hartslag meer. **Bekijken** opent waar u verder kunt; **Sluiten** verbergt die ene melding
voor de rest van uw sessie. Komt hetzelfde probleem later terug, of wordt een waarschuwing een
storing, dan staat de balk er weer. Sluiten lost niets op: de melding blijft in de widget
*Meldingen* staan tot het probleem weg is. Waarschuwingen staan niet in de balk, alleen in de
widget.

## Pop-ups

Komt er een nieuwe melding bij terwijl het overzicht openstaat, dan verschijnt ze rechtsonder als
pop-up. Welke meldingen dat doen, kiest u in uw profielmenu (standaard waarschuwingen en
storingen). Een pop-up blijft minstens 8 seconden staan; wijst u hem aan of zet u er de focus op,
dan blijft hij staan tot u er weer vanaf gaat. **Sluiten** of Escape haalt hem direct weg. Pop-ups
verschijnen alleen op het overzicht; op de andere pagina's ziet u storingen in de balk.

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
Compliance en privacy de wijzigingen en de systeemstatus. U kunt daar uw eigen indeling van
maken (zie hieronder). Op een tablet staan maximaal twee widgets naast elkaar, op een telefoon
één.

## Uw eigen indeling

Bovenaan het overzicht staan naast **Verversen** twee knoppen:

- **Widget toevoegen** toont de widgets die uw rechten toestaan en die nog niet op uw overzicht
  staan. **Toevoegen** zet er een onderaan. Een widget die u niet mag zien, wordt nooit
  aangeboden.
- **Overzicht aanpassen** toont uw widgets in hun volgorde. Met **Omhoog** en **Omlaag** schuift
  u een widget een plaats op, met **Weghalen** haalt u hem weg. Alles werkt met het toetsenbord;
  er is niets dat u moet slepen.

Zodra u iets verandert, is het uw eigen indeling; alleen u ziet hem, en hij blijft bewaard als u
opnieuw inlogt. **Standaardindeling herstellen** haalt uw eigen indeling weg, zodat u weer de
standaard van uw rol ziet. Raakt u het recht op een widget kwijt, dan verdwijnt hij vanzelf van
uw overzicht. Het aantal widgets heeft een maximum dat de superuser instelt (*Meeste widgets op
een eigen overzicht*, standaard 12). Een indeling is een persoonlijke voorkeur: ze komt niet in
het auditlog, en ze bevat geen persoonsgegevens.

## Uw profielmenu

Rechtsboven opent uw naam of uw initialen het profielmenu. Het staat op elke pagina van het
beheer en werkt zonder JavaScript. Van boven naar beneden:

- **Uw naam en rol**, en waar de rol vandaan komt: uit uw organisatie-account, dus hier niet te
  wijzigen. **Mijn rechten bekijken** opent de pagina met wat uw rol toestaat en waar.
- **Weergave**: Systeem (volgt uw apparaat), Licht of Donker. De keuze brengt u terug naar de
  pagina waar u was, geldt voor deze sessie en blijft ook na opnieuw inloggen; uitloggen zet haar
  terug op Systeem.
- **Kies een taal**: Nederlands of English. De keuze onthoudt de browser.
- **Meldingen**: of de balk bij storingen getoond wordt en welke meldingen als pop-up verschijnen
  (alle, waarschuwingen en storingen, alleen storingen, geen). **Opslaan** bewaart de keuze voor
  deze sessie.
- **Uitloggen**.

Op een telefoon blijven in de balk alleen de pictogrammen en uw initialen staan; uw naam staat in
het menu.

## De balk bovenin

Vooraan in de balk, voor het beeldmerk, klapt de **menuknop** (drie streepjes) het zijmenu in
tot zijn pictogrammen of weer uit. Ingeklapt houdt elk onderdeel zijn naam: die verschijnt als u
het pictogram aanwijst of er met het toetsenbord de focus op zet. Op een tablet begint het menu
ingeklapt en klapt de knop het uit; op een breder scherm begint het uitgeklapt. De keuze geldt
voor deze sessie en blijft ook na opnieuw inloggen, net als de weergave. Op een telefoon is er
geen zijmenu en geen knop: de balk onderin heeft de eerste onderdelen, *Meer* de rest.

Rechts van de naam van het beheer staan, van links naar rechts, kleine snelkoppelingen. U ziet
alleen wat uw rol mag openen:

- **Status** opent *Geavanceerde instellingen* met de systeemstatus (superuser en compliance).
- **Taken** springt naar de widget *Lopende taken* op dit overzicht, met het aantal lopende en
  mislukte taken ernaast. Een getal groter dan nul betekent dat er iets draait of vastzit. De
  snelkoppeling staat er alleen voor wie die widget op het overzicht heeft.
- **Meldingen** springt naar de widget *Meldingen* op dit overzicht, met het aantal open
  meldingen in uw bereik ernaast.
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
pagina's (de organisatiepagina alleen voor wie die mag openen). De keuze geldt voor deze
sessie: u ziet haar terug als u later naar het overzicht gaat, en na opnieuw inloggen staat het
filter weer op alles. Is een keuze actief, dan heeft de knop een gekleurde achtergrond, ook als er
op een kleiner scherm alleen het pictogram past. Op dit moment werkt het filter op het overzicht;
de andere lijsten hebben hun eigen filters.

## Groepen met een noodsluiting

Onder de widgets staan de groepen binnen uw bereik. **Alleen groepen met een actieve
noodsluiting** beperkt die lijst tot groepen die nu gesloten zijn, met het aantal ervan. Een
gesloten groep heeft het label **Noodsluiting actief**; wie de sluiting mag beheren, komt via dat
label direct op het tabblad **Noodsluiting** van de groep. Een sluiting die later begint, telt
nog niet mee. Het oude adres van de lijst met sluitingen leidt naar dit filter.
