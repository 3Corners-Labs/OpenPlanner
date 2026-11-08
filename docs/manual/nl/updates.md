# Updates

Op dit scherm installeert de superuser een nieuwe versie van OpenPlanner of gaat terug naar de
vorige. U opent het met **Updates** in het menu. Alleen de superuser ziet het. Het werk zelf
doet de updater op de server: die controleert de handtekening van de nieuwe versie, maakt eerst
een back-up en zet bij problemen zelf de vorige versie terug. Dit scherm dient alleen het
verzoek in en toont wat de updater meldt.

Zoekt deze installatie niet naar nieuwe versies (de hostinstelling `UPDATE_REPOSITORY` is leeg),
dan zegt het scherm dat.

## Versie

De eerste kaart toont:

- **Draait nu**: de versie die draait. Een ontwikkelbuild is geen release en krijgt geen
  updates aangeboden.
- **Commit**: de commit waaruit de versie is gebouwd, met een link als die bekend is.
- **Kanaal**: *Releases*, of *Releasekandidaten (elke merge naar main) en releases*. Een
  label meldt als de installatie **werkt zichzelf bij**.
- **Nieuwste versie**: de nieuwste gevonden versie, met een link naar de toelichting.
- **Laatst gecontroleerd**: wanneer de server voor het laatst naar een nieuwe versie zocht. Is
  dat mislukt, dan geeft een label de code.

Het kanaal, het automatisch bijwerken en het interval tussen twee controles stelt u niet hier
in, maar als superuser bij **Geavanceerde instellingen**, onderwerp Updates (`UPDATE_CHANNEL`,
`UPDATE_AUTO_APPLY`, `UPDATE_CHECK_MINUTES`; zie
[Geavanceerde instellingen](advanced-settings.md)).

<!-- field: check_now -->
### Nu controleren

De server controleert zelf op het interval dat onder de knop staat (standaard elk uur). **Nu
controleren** vraagt de worker om vroeger te controleren; de pagina toont dan *Controle
aangevraagd* tot de worker het gedaan heeft. De knop is niet beschikbaar zolang er al een
controle is aangevraagd.

## Bijwerken

Is er een nieuwere versie, dan zegt een kaart dat, met de toelichting bij de release. Twee
dingen om te weten:

- De boekingspagina's zijn tijdens de update enkele seconden niet bereikbaar. Kies een rustig
  moment.
- Een update of herstel zet de onderhoudsmodus aan vóór de back-up en weer uit als het gelukt is
  of is teruggedraaid. Mislukt het helemaal, dan blijft de onderhoudsmodus aan (zie
  [Onderhoudsmodus](maintenance.md)).

<!-- field: target -->
### Een versie kiezen

Er kunnen meerdere nieuwere versies worden aangeboden, de nieuwste bovenaan. Elke toont zijn
toelichting, of het een releasekandidaat is en wanneer hij is gepubliceerd. **Nu bijwerken naar
{version}** installeert de nieuwste; **Deze versie installeren** installeert de versie die u
kiest. Alleen nieuwere versies worden aangeboden: terug kan alleen met *Vorige versie
terugzetten*. U kunt één verzoek tegelijk indienen; zolang er een verzoek openstaat, wordt een
tweede geweigerd.

De toelichting heeft één regel per wijziging, met de titel van de wijziging. Een titel begint
met een type: `feat` is een nieuwe functie, `fix` een herstel, `docs` ontwerp of documentatie.
Een regel van het type `docs`, zoals `docs(adr): ...`, krijgt het label *ontwerp of
documentatie, niets gebouwd*: hij beschrijft een besluit, een ontwerp of een handleiding, en
geen scherm of functie die al werkt. Bestaat een versie alleen uit zulke regels, dan staat
boven de toelichting dat er niets verandert in hoe OpenPlanner werkt. Zo'n versie bijwerken kan,
maar levert geen nieuwe functie op. Het scherm zegt dit nooit van een versie zonder toelichting
of met een ingekorte toelichting. Het gaat af op de titels van de wijzigingen en vergelijkt met
de versie ervoor: slaat u bij het bijwerken versies over, lees dan ook de toelichting van de
versies ertussen.

## Het lopende verzoek

Na een verzoek tot bijwerken toont de kaart **Lopend verzoek** de stappen van de updater:
wachten tot de updater het oppakt, de versie downloaden, de handtekening controleren, een
back-up van de database maken en de nieuwe versie starten en controleren. Bij een herstel zijn
de stappen: back-up maken, de database terugzetten en starten. Elke stap is *nog niet*, *bezig*
of *klaar*. Gaat er iets mis, dan voegt de updater een stap toe om terug te draaien naar de
vorige versie en de back-up.

De lijst werkt zichzelf bij: de updater kijkt elke minuut of er een verzoek is en meldt elke
stap, en de pagina neemt dat over zonder dat u hoeft te herladen. De pagina kondigt de huidige
stap aan voor schermlezers. Zonder scripts laadt **Stand bijwerken** de pagina opnieuw.

- **Verzoek intrekken** kan zolang de updater het verzoek nog niet heeft opgepakt.
- Heeft geen updater het verzoek na 10 minuten opgepakt, dan zegt een waarschuwing dat u moet
  controleren of de updater op de server is geïnstalleerd (zie het runbook).

Is het verzoek afgelopen, dan toont de kaart de uitkomst: *Geslaagd*, *Teruggedraaid*,
*Mislukt* of *Ingetrokken*, en bij terugdraaien of mislukken de reden: de handtekening is niet
geldig, het image is niet gevonden, de back-up is mislukt, de database kon niet worden
teruggezet, de nieuwe versie werd niet gezond, of de gevraagde versie draait daarna niet.
Mislukt ook het terugzetten, volg dan het runbook.

## Vorige versie terugzetten

Na een geslaagde update biedt een kaart aan om terug te gaan naar de versie die is vervangen.
Gegevens die sinds de update zijn ingevoerd blijven bewaard. Dit kan tot 24 uur na de update.
Daarna, of zonder recente update, staat de kaart er niet.

## De melding op elke pagina

Op elke beheerpagina meldt een balk dat er een nieuwere versie is, dat een verzoek loopt (met
de huidige stap) of wat de laatste uitkomst was. Kies **Naar updates** om hier te komen.
**Herinner me over 60 minuten** verbergt de balk 60 minuten, alleen in uw huidige sessie.

## Geschiedenis

De tabel toont elk verzoek en elke uitkomst met het tijdstip, wat er gebeurde (update of
herstel aangevraagd, geslaagd, mislukt of teruggedraaid, aanvraag ingetrokken), van welke
versie naar welke, en door wie: een persoon, het systeem (automatische updates), de updater op
de server of de server zelf buiten het portaal. Is de versie buiten het portaal gewisseld (door
het installatieprogramma of met de hand op de server), dan meldt een bericht dat. Dezelfde
gebeurtenissen staan in de auditlog; een link gaat naar de update-gebeurtenissen daar.
