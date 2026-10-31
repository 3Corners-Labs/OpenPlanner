# Geavanceerde instellingen

Dit scherm laat zien hoe de installatie is ingesteld en waar elke waarde vandaan komt. U opent
het via **Geavanceerde instellingen** in het menu. De superuser en de rol Compliance en privacy
kunnen het bekijken. De superuser kan een deel van de instellingen wijzigen; de auditor leest
alleen.

Geheime waarden, zoals wachtwoorden en sleutels, worden nooit getoond: alleen of ze zijn
ingesteld.

## Systeemstatus

De instellingen die alleen de beheerder van de server zet, in de omgeving van de server of in
een bestand. Ze zijn nodig voordat het portaal of de database werkt, of ze horen bij de server
zelf. Per instelling ziet u of die is ingesteld en waar die vandaan komt, nooit de waarde.

Draait de installatie niet als productie (bijvoorbeeld een testomgeving), dan staat hier ook
wat productie zou weigeren: een ontbrekende sleutel of een adres zonder https. Productie start
nooit met een van deze punten.

### Certificaat van het publieke adres

Onder de omgeving staat het certificaat waarmee bezoekers de installatie bereiken: het adres,
wie het heeft uitgegeven, tot wanneer het geldig is (met het aantal dagen dat nog rest), wanneer
het voor het laatst is gecontroleerd en één bevinding:

- **In orde**: het certificaat wordt vertrouwd en is nog ruim geldig. De verlenging gaat
  vanzelf.
- **Verloopt binnenkort**: er zijn minder dagen over dan de instelling *Waarschuwen bij
  certificaat korter geldig dan*. De automatische verlenging lijkt te mislukken; waarschuw de
  beheerder van de server.
- **Niet vertrouwd**: het certificaat komt van een test-, eigen of onbekende
  certificaatautoriteit. Normaal tijdens een proefinstallatie met de testomgeving van Let's
  Encrypt; in productie waarschuwen browsers bezoekers.
- **Verlopen**, **verkeerde naam**, **nog niet geldig**, **niet bereikbaar**, **geen https**:
  de tekst bij de bevinding zegt wat er aan de hand is.

De server controleert het certificaat elke paar uur (instelling *Certificaat controleren
elke*). Een bevinding die niet *in orde* is, staat ook in het auditlog als systeemgebeurtenis
`tls.certificate`, en wordt herhaald zolang hij duurt (instelling *Melding over het certificaat
herhalen elke*). Staat er *Nog niet gecontroleerd*, dan heeft de server nog geen ronde gedraaid.

**Nu controleren** (superuser) controleert het certificaat meteen in plaats van bij de volgende
ronde, bijvoorbeeld na het wisselen van de bron hieronder.

De ondersteuning kan u om een schermafdruk van dit scherm vragen in plaats van de configuratie
van de server.

### Bron van het certificaat

Bij een kleine installatie verzorgt de meegeleverde HTTPS-proxy het certificaat, en kiest de
superuser waar het vandaan komt:

- **Let's Encrypt (automatisch)**, de standaard: de proxy haalt het certificaat zelf en verlengt
  het; u hoeft niets te doen.
- **Eigen certificaat**: de proxy gebruikt het certificaat dat u hier plaatst, bijvoorbeeld een
  PKIoverheid-certificaat of een ander organisatiegevalideerd certificaat. De verlenging is dan
  uw zaak: plaats de nieuwe keten voordat de oude verloopt; de bevinding *verloopt binnenkort*
  hierboven is de herinnering.

De privésleutel van een eigen certificaat wordt op deze server gemaakt en verlaat die nooit:
hij staat niet in de database, niet in een back-up daarvan, en wordt nooit getoond. Zo krijgt u
een eigen certificaat:

1. Kies **Nieuwe sleutel en aanvraag maken**. Het scherm toont een certificaataanvraag (CSR)
   voor het publieke adres, als tekst en als download. Een aanvraag stoort een certificaat dat
   al in gebruik is niet; een nieuwe aanvraag vervangt een eerdere.
2. Lever de aanvraag in bij uw certificaatautoriteit. U ontvangt een certificaatketen (PEM).
3. Plaats de keten onder **Certificaatketen plaatsen of vervangen** (servercertificaat eerst,
   daarna de tussencertificaten). De upload wordt gecontroleerd voordat er iets wordt
   opgeslagen: het bestand moet leesbaar zijn, de keten moet horen bij de hier gemaakte sleutel
   (of bij de sleutel die al in gebruik is, bij een verlenging), het certificaat moet geldig
   zijn voor het publieke adres van deze installatie, en het mag niet verlopen of nog niet
   geldig zijn. Een weigering noemt de reden bij het veld. Het scherm toont daarna de
   vingerafdruk, het onderwerp, de namen, de vervaldatum, het moment van plaatsen en dat de
   sleutel op deze server aanwezig is.
4. Kies **Eigen certificaat** als bron en sla op. De proxy schakelt binnen ongeveer een halve
   minuut om; kies **Nu controleren** om het resultaat te zien.

Om een bestaand certificaat eenmalig over te zetten plaatst u de keten en, in het tweede veld,
de bijbehorende privésleutel samen; de sleutel gaat rechtstreeks naar de opslag van de proxy op
deze server en wordt nergens anders bewaard.

Terugschakelen naar **Let's Encrypt (automatisch)** werkt op dezelfde manier; het certificaat en
de sleutel blijven bewaard voor later. **Geüpload certificaat verwijderen** wist het certificaat
(nooit de sleutel) en kan alleen zolang Let's Encrypt de bron is.

**Privésleutel ontbreekt op deze server** verschijnt als de database op een andere server is
teruggezet: het certificaat reisde mee, de sleutel niet. Maak een nieuwe aanvraag en plaats de
keten, of zet het paar opnieuw over; tot die tijd gebruikt de proxy Let's Encrypt.

Elke aanvraag, upload en verwijdering staat in het auditlog met de vingerafdruk en de
vervaldatum van het certificaat, nooit een sleutel. Voor al deze wijzigingen is een recente
inlog nodig.

Beheert de serverbeheerder het certificaat op de server zelf, dan is de instelling **vastgezet
door de beheerder van de server** en zijn er geen formulieren. Eindigt HTTPS bij een eigen
reverse proxy of load balancer, dan meldt het scherm dat de bron niet van toepassing is: het
certificaat wordt daar beheerd, en het blok hierboven toont nog steeds wat bezoekers zien.

## Platforminstellingen

Onder de systeemstatus staat elke instelling van het platform per onderwerp, met de huidige
waarde en de bron:

- **Standaardwaarde**: de waarde waarmee de software wordt geleverd.
- **Beheerportaal**: een waarde die in het portaal is ingesteld.
- **Omgeving**: een waarde die de beheerder van de server heeft gezet. De instelling is dan
  **vastgezet door de beheerder van de server**, en het scherm noemt de variabele. Een waarde
  in het portaal geldt niet zolang die vastzetting er is.
- **Bestand**: een geheim dat de server uit een bestand leest.

Een gewijzigde waarde geldt binnen ongeveer tien seconden, zonder herstart.

## Een instelling wijzigen

De superuser wijzigt de instellingen voor bedrijfsvoering, bewaartermijnen, opslag,
snelheidslimieten, sessies van het beheerportaal, medewerkers en de planner, de reservemailserver,
de updatecontrole en de Webex Service App met het WxCC-adres. De webhookgeheimen (die de server
zelf maakt) en de inlogkoppeling volgen in een latere versie. Na een gewijzigde Webex-waarde
worden videogesprekken en werkitems binnen ongeveer tien seconden opnieuw gekoppeld. Zet bij een nieuwe Service App de client-ID, het client secret en het refresh token tegelijk.

- Elk onderwerp is één formulier met één knop **Opslaan** onderaan. Vul bij de instellingen die
  u wilt wijzigen de nieuwe waarde in (aan of uit kiest u uit een lijst) en kies **Opslaan**;
  alleen de waarden die afwijken van de huidige worden opgeslagen. Het portaal accepteert alleen
  waarden binnen zijn grenzen, bijvoorbeeld 1 tot 30 afspraken per uur per adres. Een waarde
  daarbuiten wordt bij de regel geweigerd, met de reden; de waarden erboven in hetzelfde
  formulier zijn dan al opgeslagen, die eronder nog niet.
- Sommige instellingen hangen van elkaar af: kritieke opslag moet onder weinig opslag blijven, en
  de herinnering om opnieuw te boeken moet vóór het vervallen komen. Een waarde die een andere
  instelling tegenspreekt, wordt geweigerd.
- **Standaardwaarde gebruiken** haalt de hier ingestelde waarde weg; dan geldt weer de
  standaardwaarde (of de waarde van de server).
- Een instelling die **vastgezet is door de beheerder van de server** heeft geen veld:
  wijzig die in de omgeving van de server, of vraag het de beheerder. **Beheer dit voortaan hier**
  neemt de waarde van de server over in het portaal; vraag de beheerder daarna de variabele die
  bij de regel staat weg te halen en te herstarten. Tot dan geldt de waarde van de server nog.
- **Wachtwoorden en tokens** (het wachtwoord van de mailserver, het leestoken van de
  updatecontrole) kunt u alleen schrijven: het scherm zegt alleen of er een is ingesteld. Laat het
  veld leeg om de huidige te houden; **Standaardwaarde gebruiken** haalt hem weg. Ze worden
  versleuteld opgeslagen en vragen de `SETTINGS_ENCRYPTION_KEY` van de server.
- Het **logniveau** valt altijd vanzelf terug naar de standaard, na hooguit 24 uur; het niveau
  TRACE na hooguit de uren van *TRACE_LEVEL_MAX_HOURS* (standaard 4). Vul in na hoeveel uur.
  Persoonsgegevens blijven op elk niveau gemaskeerd in het log; TRACE-regels dragen alleen de
  vaste velden van `docs/logging.md`.

Voor een wijziging moet u kort geleden zijn ingelogd. Elke wijziging staat in het auditlog met
de oude en de nieuwe waarde; een geweigerde wijziging ook.

## De inlogkoppeling wijzigen

Alleen de superuser kan wijzigen welke claims van de identity provider de rollen, organisaties
en groepen dragen, en welke waarde welke rol is. Vul **Inlogkoppeling wijzigen** in en kies
**Testen met een nieuwe login**. U wordt naar de identity provider gestuurd om opnieuw in te
loggen. De nieuwe koppeling wordt op die login toegepast: hij wordt alleen bewaard als u er nog
superuser mee bent. Anders verandert er niets en meldt het scherm dat. Zo kan niemand per
ongeluk iedereen buitensluiten.

Is een waarde vastgezet door de beheerder van de server, dan is de hele koppeling van de
beheerder en staat het formulier er niet. Verandert de identity provider later een claim of
rolwaarde, dan kan de beheerder van de server de koppeling vanaf de server herstellen.

## Opmerkingen

- **Buiten de grenzen van het portaal**: de beheerder van de server heeft een waarde gezet die
  het portaal zelf niet zou accepteren, zoals een heel korte bewaartermijn. Die geldt wel, maar
  vraag de beheerder of dat zo bedoeld is.
- **Opgeslagen waarde voldoet niet aan de regels en wordt genegeerd**: een opgeslagen waarde is
  niet meer geldig, bijvoorbeeld omdat die buiten het portaal is aangepast. Dan geldt de
  standaardwaarde of de waarde van de server.
