# Rapporten

De rapporten laten zien hoe uw groepen worden gebruikt: hoeveel boekingen er worden gemaakt, hoe ze
eindigen en hoe ver vooruit mensen boeken. U opent de lijst met **Rapporten** in het menu, en de
cijfers van een groep of organisatie met het tabblad **Gebruik** onder *Overzicht*.
Organisatiebeheerders, groepsbeheerders en de rol Compliance en privacy kunnen ze bekijken, elk voor
wat ze mogen zien; de superuser ziet alles. Een medewerker aan het werk heeft geen rapporten.

De rapporten bevatten alleen aantallen. Ze tonen nooit een naam, een adres, een telefoonnummer of
van welke boeking een cijfer komt, en ze worden niet gemaakt uit de afspraken: er wordt geteld
wanneer een boeking verandert, één klein gegeven per wijziging.

## Een tabel lezen

- **Dagen** zijn hele dagen in de tijdzone van de groep zelf. Kies een periode met **Van** en
  **Tot**; zonder keuze ziet u de laatste vier weken. De meest recente dag is *voorlopig*: hoe de
  boekingen daarvan eindigen, staat nog niet vast.
- **Cijfers van** zegt wanneer de aantallen voor het laatst zijn bijgewerkt. Is dat lang geleden,
  dan waarschuwt Systeemstatus daarvoor.
- **Een verborgen aantal** staat er als *< 5*. Een aantal onder het minimum zou naar één persoon
  kunnen wijzen en wordt daarom niet getoond, en het totaal van een tabel ook niet als het een
  verborgen aantal zou verraden door af te trekken. Dat is geen fout en het kan niet worden
  uitgezet: de platformbeheerder stelt het minimum in (nooit onder 3), en uw organisatie mag het
  voor de eigen rapporten verhogen, nooit verlagen.

## De tabellen

- **Boekingen gemaakt per dag**: nieuwe boekingen, zonder de boekingen die zijn ontstaan door een
  andere te verplaatsen.
- **Hoe boekingen eindigden**: geannuleerd door de deelnemer, door de planner, na een sluiting of
  door het systeem, en niet op tijd bevestigd. *Afgerond* en *niet verschenen* worden twee uur na
  de afspraak uit het videogesprek afgeleid: een deelnemer die samen met de gespreksleider in de
  vergadering is gezien, is afgerond; een deelnemer die nooit is gezien in een vergadering die was
  aangemaakt, is niet verschenen. Meldt het platform niet wie er binnenkwam, dan telt een afspraak
  als geen van beide.
- **Hoe ver vooruit boekingen worden gemaakt**: de tijd tussen het boeken en de afspraak, in vaste
  klassen, met het gemiddelde. Tijden worden op het kwartier bijgehouden.
- **Annuleringen door deelnemers**: hoeveel tijd er nog was voor de afspraak.
- **Verplaatste boekingen**: door wie ze zijn verplaatst.
- **Bevestiging per e-mail**: boekingen die zijn bevestigd tegenover boekingen die zijn vervallen,
  met het percentage bevestigd.
- **Noodsluitingen**: boekingen in de wacht gezet en weer bevestigd.
- **Boekingen van voor de rapportage**: uitkomsten van boekingen die in dagtellingen waren
  samengevoegd voordat het gebruik werd geteld; meer is er niet van bekend.

## De tegels, de grafieken en *Uitsplitsen naar*

Bovenaan **Volume** staan vier tegels die het antwoord geven voordat u de tabellen leest: afspraken in de periode, het no-showpercentage, het aandeel dat door deelnemers is geannuleerd (met hoeveel daarvan minder dan een dag voor de start) en het aandeel bevestigd na de e-mail (met de klasse waarin de mediane tijd valt). **Capaciteit** heeft er ook vier: bezetting (met geboekte van alle plaatsen), ongebruikte plaatsen, volle blokken en het tekort aan medewerkers. Een tegel die uit een verborgen getal volgt, zegt *niet beschikbaar*; hij toont nooit een getal dat de tabellen verbergen. Onder elke tabel die een aantal verbergt, staat een zin waarom, ook dat er nog één aantal extra verborgen wordt als een enkel aantal het totaal zou verraden.

Het bereikformulier van **Volume** heeft **Uitsplitsen naar**: geen, wachtrijmethode of taal. Een groep doet alleen mee als haar eigen totaal in de periode minstens vijf keer het minimum is; de pagina zegt hoeveel groepen buiten beschouwing bleven. Uitsplitsen naar onderwerp kan nog niet.

## De rapportfamilies

Bovenaan de pagina Gebruik schakelt een rij links tussen vier families van hetzelfde rapport (een vijfde, *Team*, is voor organisatiebeheerders wier organisatie het aanzette, en een zesde, *Wallboard*, voor wie het recht heeft om het te tonen). De
gekozen periode blijft staan; elke familie heeft een eigen adres dat u kunt bewaren. De eerste,
**Volume**, is de set tabellen hierboven; de andere staan hieronder.

### Wachtrij en gesprekken

Hoe boekingen bij medewerkers komen en hoe de gesprekken verlopen.

- **Hoe boekingen bij medewerkers komen**: boekingen per wachtrijmethode (contactcenter of de eigen
  wachtrij) en per soort (gepland of direct gesprek), met totalen.
- **Wachttijd tot een gesprek verbindt** (Q2): de tijd vanaf het aanmaken van een taak tot het
  gesprek verbindt, in vaste klassen, met het gemiddelde.
- **Wanneer de gastheer binnenkwam, ten opzichte van de start** (Q3): te vroeg staat er met *te
  vroeg*, te laat met *te laat*. Alleen geplande gesprekken tellen mee.
- **Deelnemer wacht op de gastheer** (Q4) en **hoe lang gesprekken duren** (Q5), met het
  gemiddelde, het aantal afgeronde gesprekken en het gemiddeld aantal personen per gesprek (U11).
- **Directe gesprekken** (Q6): gestart, weggegaan tijdens het wachten, niet op tijd opgepakt en
  het aandeel dat wegging, met de wachttijd op het moment van weggaan.
- **Eigen wachtrij: oppakken** (Q7): opgepakt, teruggegeven, teruggegeven per keer oppakken en de
  tijd tot een boeking wordt opgepakt. Het aandeel opgepakte boekingen staat er niet: wat is
  aangeboden wordt niet vastgelegd.
- **Fouten bij het doorzetten** (Q8): per code uit een vaste lijst, nooit de tekst van het andere
  systeem.
- **Directe gesprekken tegelijk** (Q9): het hoogste aantal dat tegelijk wachtte of verbonden was,
  per dag.
- **Voorgestelde nieuwe tijden** (U8): voorgesteld na vraag van de planner of door de assistent,
  geaccepteerd, verlopen, het aandeel geaccepteerd en de tijd tot accepteren.

### Capaciteit

Hoe vol de agenda is en wanneer het druk is.

- **Bezettingsgraad per dag** (C1, C3): de boekingen die meetellen tegenover de capaciteit van de
  momenten die begonnen zijn, en de plaatsen die niemand boekte. Een dag met weinig momenten is
  verborgen, en een percentage dat maar een paar mensen vertegenwoordigt ook.
- **Hoe vol de momenten waren** (C2): leeg, minder dan half, meer dan half en vol.
- **Drukste dagen en uren** (C4): een tabel van weekdag bij uur, in de eigen tijd van de groep. In
  elke cel staat het getal; de tint helpt alleen het oog. De tabel verschijnt bij een periode van
  minstens 7 dagen en alleen voor groepen met in die periode minstens vijf keer het minimum aan
  boekingen; kleinere groepen vallen buiten deze tabel, en de pagina zegt hoeveel.
- **Blokken met te weinig bezetting** (C5): nog steeds te weinig, te weinig maar geaccepteerd door
  een planner, opgelost.

### Kanalen

Hoe berichten aankomen, in welke taal mensen boeken en hoe het proefgesprek verloopt.

- **Berichten verstuurd en mislukt** (M1): per doel en per kanaal, met het aandeel dat mislukte.
- **Boekingen per taal** (M2): alleen voor groepen die groot genoeg zijn; kleinere groepen vallen
  buiten de tabel en de pagina zegt hoeveel.
- **Proefgesprekken** (T1): aangemaakt, de link gevolgd, in de vergadering gekomen en mislukt. Het
  verschil tussen de laatste twee zijn mensen die in de lobby bleven hangen.

## Grafieken

Waar een grafiek helpt, staat die boven de eigen tabel, en de tabel bevat altijd dezelfde cijfers,
zodat niets alleen in het plaatje staat. Een grafiek heeft een titel en een beschrijving voor
schermlezers, reeksen verschillen in patroon of lijnstijl en niet alleen in kleur, en een
verborgen aantal is een gestippelde markering van één vaste hoogte met het label *< 5*: die zegt
niets over het echte getal. De grafieken hebben geen script nodig, schuiven op een smal scherm
zijwaarts in hun kader en blijven weg bij het afdrukken; de tabellen blijven. Is voor een familie
in de periode niets vastgelegd, dan zegt de pagina dat.

Met scripts aan kunt u een grafiek ook verkennen: beweeg erover of focus erop en gebruik de pijltjestoetsen voor een tip met de cijfers van die categorie, zet een reeks uit met de knop ervan, download de grafiek als SVG of PNG, en druk op Enter bij een dag om alleen die dag te zien (de dag is een gewone koppeling in de tabel, dus het werkt ook zonder scripts). Zonder scripts staan de grafieken en tabellen er gewoon.

## Downloaden als bestand

Wie mag exporteren (de superuser, organisatiebeheerders, groepsbeheerders en de rol Compliance en privacy) ziet onder de periode op een Gebruik-pagina twee koppelingen.

- **Dit rapport downloaden als CSV** geeft precies de tabellen van de familie waar u naar kijkt, na de regel voor kleine aantallen: een verborgen cijfer is een lege waarde met de opmerking *hidden*. Het bestand bevat geen interfacetekst, alleen codes (een dag, een uitkomst, een bucketnummer), dus het leest in elke taal hetzelfde.
- **Alle uurcijfers downloaden als CSV** geeft één rij per uur, groep, maat en snede, voor analyse in uw eigen hulpmiddel (het formaat `openplanner.usage/1`, beschreven in `docs/reporting.md`). Een cel onder het minimum staat helemaal niet in het bestand.

Beide openen in Excel (UTF-8, `;` als scheidingsteken). De platformbeheerder bepaalt hoeveel rijen en dagen één bestand mag bevatten; een langere periode wordt met een melding geweigerd en het bestand wordt niet afgekapt. Elke download staat in het auditlog met wie, welke familie, welke periode, hoeveel rijen en een controlegetal van het bestand, nooit de cijfers. Het controlegetal staat ook in de header `X-Content-SHA256` van het antwoord. Net als bij de export van het auditlog geeft een link naar een download op een andere website in een actuele browser een foutmelding, zodat een andere website geen download op uw naam in het log kan zetten.

## De samenvatting per e-mail

Organisatiebeheerders zien onderaan de pagina *Gebruik* van de organisatie een onderdeel **Samenvatting per e-mail**. Een abonnement geldt voor de hele organisatie of voor één groep en heeft een periode: elke dag, elke week (op een weekdag) of elke maand (op een dag van 1 tot 28). De mail gaat op het verzenduur van de organisatie (7:00, tenzij aangepast) in de tijdzone van de groep, beslaat de dagen sinds de vorige mail tot en met gisteren en bevat de kerncijfers (gemaakte boekingen, niet verschenen, de gemiddelde wachttijd tot een gesprek verbindt, bezettingsgraad) en een koppeling naar het rapport van die dagen. Een verborgen cijfer staat als streepje. Zet uw organisatie de cijfers uit, dan blijft alleen de koppeling over.

Ontvangers zijn collega's die de rapporten van dat bereik mogen zien, gekozen uit een lijst. Er zijn geen vrije e-mailadressen en er gaat geen bestand mee: de koppeling leidt naar het rapport, waar u zoals altijd inlogt. Het recht wordt bij het versturen opnieuw gecontroleerd. Een collega die het kwijt is, wordt overgeslagen en de lijst zegt hoeveel; een collega zonder bekend adres ook. Het adres is het geverifieerde e-mailadres dat de inlogdienst bij de laatste login van de collega doorgaf (te zien op de pagina van de collega onder *Medewerkers*); een collega die sindsdien niet is ingelogd, wordt opgezocht in de meegeleverde inlogdienst. Een gemiste mail wordt nog tot 24 uur later verstuurd; daarna wordt hij overgeslagen en de lijst toont hoe de laatste mail verliep. Elke wijziging van een abonnement staat in het auditlog.

### Team

De werklast per teamlid: afgehandelde gesprekken, minuten in gesprek, uit de eigen wachtrij opgepakte boekingen en het aandeel geplande gesprekken waarbij de gastheer op tijd binnenkwam. Alleen voor organisatiebeheerders (het recht *De werklast per teamlid in de rapporten bekijken*) en **standaard uit**: zolang uw organisatie bij de instellingen **Toon de werklast per teamlid in de rapporten** niet aanzet, wordt er niets over een persoon vastgelegd en staat bij *Team* op de pagina Gebruik *uit*, met een koppeling naar de instelling. Aanzetten telt vanaf dat moment; eerdere gesprekken doen niet mee, en uitzetten stopt het tellen weer (wat geteld is, blijft tot het einde van de bewaartermijn).

De tabel staat op naam. Er is geen rangorde, geen *top*- of *slechtste*-lijst, geen totaalrij en geen indeling naar groep of uur. Een getal onder het minimum is verborgen en staat als *< 5*; de minuten gaan mee met de gesprekken. Alleen gesprekken die een medewerker uit de eigen wachtrij heeft opgepakt, worden aan een persoon toegerekend; een gesprek dat het contactcenter afhandelde, heeft in deze installatie geen teamlid. Wordt een teamlid verwijderd, dan worden zijn of haar cijfers één rij, *Voormalige teamleden*, zodat de geschiedenis haar totalen houdt en er geen persoon overblijft. De Team-familie staat niet in de CSV-bestanden en niet in de machine-interface.

### Wallboard

Een live weergave van vandaag voor een scherm in de teamruimte, voor organisatiebeheerders en groepsbeheerders (het recht *Het live wallboard van de groepen tonen*). Het is de koppeling **Wallboard** op de pagina Gebruik, van een groep of van de groepen van de organisatie, en **Open schermvullend** toont het zonder menu. Het heeft zes tegels, elk met de woorden voor wat het is en *per* wanneer: **Wachtend nu**, **Langste wachttijd**, **In gesprek**, **Afspraken vandaag** (met de no-shows tot nu toe), **Bezetting huidig blok** (met het volgende blok) en **Mislukte doorzettingen**, dit uur en het uur ervoor. Eronder staan de afspraken per uur van vandaag, het deel van de dag dat nog komt vaag gearceerd.

Alleen aantallen: geen naam, referentie of link. De drie live tegels (wachtend, langste wachttijd, in gesprek) zijn exact en komen uit de eigen wachtrij; je ziet ze voor groepen met een eigen wachtrij en alleen als je de wachtrij ook mag werken, anders zegt de tegel dat. De andere tegels komen uit dezelfde uurstatistieken als de andere rapporten: een getal onder het minimum staat als *< 5*, en een percentage zie je alleen als het aantal boekingen erachter ook te zien is. Die tegels zeggen per wanneer de statistieken voor het laatst zijn bijgewerkt, wat eerder kan zijn dan de live tegels.

Het bord vraagt om de paar seconden verse getallen (standaard 10; de platformbeheerder stelt het in) en heeft daarvoor een script nodig; zonder script toont het wat het bij het laden had en een koppeling **Nu verversen**. **Pauzeer verversen** houdt de getallen vast, en het bord zegt dat. Lukt verversen drie keer achter elkaar niet, of is de aanmelding verlopen, dan zegt het bord in woorden dat de gegevens oud zijn sinds die tijd en worden de getallen doorgestreept, zodat een stilgevallen scherm nooit voor actueel wordt aangezien. Een wachttijd boven de grens van uw organisatie (standaard 5 minuten, **Minuten wachten waarna het wallboard lange wachttijd meldt** bij de instellingen van de organisatie) toont de tegel met de woorden *lange wachttijd* en wordt één keer aangekondigd; er is geen geluid en niets knippert. De tekst groeit mee met het scherm en is bedoeld voor een 1080p-scherm.

#### Displaylinks

Voor een scherm waar niemand inlogt kunnen organisatiebeheerders een **displaylink** maken (het recht *Displaylinks voor het wallboard maken en intrekken*). Displaylinks staan **uit** zolang uw organisatie bij de instellingen **Displaylinks voor het wallboard toestaan** niet aanzet. Kies onder het wallboard van de pagina Gebruik van de organisatie welk bord (een groep of alle groepen), een naam voor het scherm, hoe lang het geldig is (hoogstens wat het platform toestaat, standaard 30 dagen) en eventueel de netwerken die het mag gebruiken en of het op het eerste adres wordt vastgepind, en kies **Link maken**.

Het adres wordt **één keer** getoond, op de pagina die het formulier beantwoordt, en kan niet opnieuw worden getoond; kopieer het dus meteen. Het kan **één keer** worden geopend: het eerste verzoek opent het scherm en zet er een cookie op, en wie het adres later probeert wordt geweigerd, zodat een link die achteraf uitlekt niets waard is. Een vervangen scherm heeft een nieuwe link nodig. De link toont dit wallboard en niets anders van het portaal, en nooit de *Team*-weergave.

*Netwerken* is een lijst van reeksen, één per regel; leeg betekent elk netwerk, tenzij uw organisatie een standaardlijst heeft, die een link alleen kan versmallen. Met **Vastpinnen op het eerste adres** wordt het adres dat het scherm bij het eerste gebruik heeft vastgelegd (een IPv4-adres als zichzelf, een IPv6-adres als zijn /64) en werkt het scherm alleen vanaf dat adres. De netwerken en de pin worden bij elke verversing gecontroleerd, niet alleen bij het begin. Een verzoek van een ander netwerk of adres wordt geweigerd met een duidelijke melding, de link blijft geldig voor het juiste adres, en één regel in het auditlog legt het vast (de reden, niet het adres). Een scherm waarvan het adres wisselt heeft een nieuwe link nodig.

De lijst toont elke link met zijn status (nog niet geopend, actief, verlopen, ingetrokken), het gepinde adres, de netwerken, wanneer hij voor het laatst is gebruikt en wanneer hij verloopt. **Intrekken** beëindigt het scherm binnen één verversing. Maken, eerste gebruik en intrekken staan in het auditlog; het adres van een link staat er nooit in.

## Wat hier nog niet staat

Het aandeel van de bezette tijd dat in gesprekken gaat (bezetting van het team) volgt in een latere stap.
