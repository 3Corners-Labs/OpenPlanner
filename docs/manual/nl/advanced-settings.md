# Geavanceerde instellingen

Dit scherm laat zien hoe de installatie is ingesteld en waar elke waarde vandaan komt. U opent
het via **Geavanceerde instellingen** in het menu. De superuser en de rol Compliance en privacy
kunnen het bekijken. De superuser kan een deel van de instellingen wijzigen; de auditor leest
alleen.

Geheime waarden, zoals wachtwoorden en sleutels, worden nooit getoond: alleen of ze zijn
ingesteld.

Het scherm heeft drie tabbladen onder de titel:

- **Systeemstatus**: de omgeving, het certificaat en de gegevensvelden;
- **Instellingen**: alle platforminstellingen per onderwerp, met bovenaan *Op deze pagina*, een
  lijst met links naar elk onderwerp, en de proef van de inlogkoppeling;
- **Configuratiepakket** (alleen de superuser): exporteren en importeren.

Een wijziging of een foutmelding verschijnt op het tabblad van het formulier dat u gebruikte.

## Systeemstatus

De instellingen die alleen de beheerder van de server zet, in de omgeving van de server of in
een bestand. Ze zijn nodig voordat het portaal of de database werkt, of ze horen bij de server
zelf. Per instelling ziet u of die is ingesteld en waar die vandaan komt, nooit de waarde.

Draait de installatie niet als productie (bijvoorbeeld een testomgeving), dan staat hier ook
wat productie zou weigeren: een ontbrekende sleutel of een adres zonder https. Productie start
nooit met een van deze punten.

### Sleutelrotatie

Zolang `SETTINGS_ENCRYPTION_KEY_PREVIOUS` of `MANAGEMENT_TOKEN_KEY_PREVIOUS` is gezet, toont
het blok *Sleutelrotatie* hoeveel versleutelde geheimen nog onder de vorige instellingensleutel
staan en hoeveel lopende boekingen nog een koppeling onder de vorige tokensleutel hebben. Er
staan alleen aantallen, nooit sleutels of inhoud. Bij 0 staat er dat de vorige sleutel weg kan;
anders staat er wat u eerst doet (`rekey` op de host uitvoeren, of wachten tot die boekingen
voorbij zijn). Zonder vorige sleutel verschijnt het blok niet. De werkwijze staat in het
draaiboek, *Rotating secrets*, en de inventaris van alle sleutels in `docs/cryptography.md`.

### Back-ups van updates

Vóór elke update kopieert de updater de database om te kunnen terugdraaien. Die kopie bevat
persoonsgegevens en wordt daarom na 24 uur verwijderd, door de updater en als tweede vangnet
door de worker, ook als de updater of zijn timer niet draait. Het blok toont hoeveel kopieën er
zijn en hoe oud de oudste is. Is de oudste meer dan twee uur over zijn 24 uur, dan staat er een
waarschuwing: geen van beide verwijdert nog, dus controleer of de worker draait en of het
datavolume schrijfbaar is. Bestandsnamen worden nooit getoond. Back-ups voor disaster recovery
zijn een zaak van de hostingpartij, niet van dit scherm (draaiboek, *Backup and restore*).

### Adres en proxy

Boven het certificaat staat wat de installatie over haar eigen adres weet. Elke link in een
e-mail, de inlogredirect en de widget is gebouwd op het publieke adres, nooit op het adres van
het verzoek; dat ene adres bepaalt of deelnemers de installatie bereiken. Het blok toont:

- **Installatie**: de meegeleverde proxy, een eigen reverse proxy of geen proxy (een lab);
- **Publiek adres** en, als het anders is, het **Webhook-adres**;
- het **vertrouwde proxyadres** (`FORWARDED_ALLOW_IPS`);
- de **OIDC-redirect-URI**, om te vergelijken met wat bij de identiteitsprovider is
  geregistreerd, en de **widget-loader**, om te vergelijken met de site die de widget insluit.

Deze waarden staan op de server, in de omgeving (klasse B): ze zijn hier niet te wijzigen,
omdat een verkeerd publiek adres iedere beheerder bij de volgende login buitensluit. Een
wijziging vraagt een herstart; de werkwijze staat in het draaiboek, *Address and proxy*.

Onder **Rondgang** staat de uitkomst van de controle die de server om de paar uur doet: hij
vraagt het publieke adres om `/api/v1/instance`, zoals de browser van een deelnemer dat doet, en
beoordeelt het antwoord met één bevinding en het tijdstip. De bevindingen zijn **in orde**,
**niet bereikbaar** (met de reden van het netwerk of de server), **andere installatie**,
**geen https**, **proxy niet vertrouwd**, **geen proxy-headers** en **doorverwezen**. Bij
*proxy niet vertrouwd* staat `FORWARDED_ALLOW_IPS` niet op het adres waarmee de web-container de
proxy ziet verbinden: alle bezoekers delen dan één adres en de eigen redirects wijzen naar
`http://`. Is de controle op de server uitgezet (`PUBLIC_ADDRESS_CHECK_ENABLED`), dan staat er
*niet gecontroleerd*, nooit *in orde*.

Ziet de webserver verzoeken met `X-Forwarded-For` of `X-Forwarded-Proto` van een proxy die hij
niet vertrouwt, dan staat dat er ook, met het aantal en de soort afzender (loopback, privé of
publiek). Het adres staat er alleen als het een proxy in het eigen netwerk is, nooit dat van een
bezoeker. Elke bevinding die niet *in orde* is, komt ook in het auditlog als systeemgebeurtenis
`public_address`, en wordt herhaald zolang hij duurt (instelling *Melding over het certificaat
herhalen elke*). In de Overzicht-widget Systeemstatus staat dan één rij *Proxy* die hierheen
verwijst.

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
- Twee instellingen van de planner waarschuwen bij veel acties van één persoon binnen een uur:
  **Waarschuwing bij wijzigingen per planner per uur** (standaard 50, 5 tot 1000) en
  **Waarschuwing bij verzoeken om contactgegevens per persoon per uur** (standaard 20, 5 tot
  500). Boven de grens komt er één waarschuwing in het auditlog met wie het was en hoe vaak. De
  wijzigingen en verzoeken zelf gaan gewoon door: zo valt een gestolen sessie of misbruik op,
  zonder dat een planner wordt tegengehouden. Uitzetten kan niet.

Voor een wijziging moet u kort geleden zijn ingelogd. Elke wijziging staat in het auditlog met
de oude en de nieuwe waarde; een geweigerde wijziging ook.

## Configuratiepakket

**Als JSON-document.** Het bovenste blok van dit tabblad exporteert de hele configuratie, of
leest een document in. Een document is een JSON-bestand dat u met de hand kunt aanpassen:
objecten worden met hun eigen sleutel genoemd, nooit met nummers, dus het blijft geldig tussen
versies, en het gepubliceerde schema (`docs/configuration-schema/v1.json`) laat een editor
veldnamen aanvullen en een typefout signaleren. Kies **Document exporteren**, met of zonder
geheimen (verzegeld met een wachtwoord van minstens 16 tekens, herhaald). Om er een in te
lezen kiest u het bestand en **Plan tonen**: u ziet per object of het wordt aangemaakt,
gewijzigd of verwijderd en welke velden, nooit de waarden, en elke weigering die het stopt.
Pas dan schrijft **Plan toepassen** alles in één keer, of niets als een onderdeel mislukt. Een
document met verzegelde geheimen vraagt opnieuw om zijn wachtwoord. Een document in de stand
*replace* verwijdert wat het niet noemt; lees het plan dus zorgvuldig. Het document van één
organisatie staat onder **Overzicht → Configuratie** van die organisatie. Elke toegepaste
wijziging staat in het auditlog, zonder waarden, en u moet recent hebben ingelogd.

**Als oud SQLite-pakket (nog één release gelezen).** Eerdere versies exporteerden de hele
configuratie als SQLite-bestand. Die export is vervallen: gebruik hierboven **Document
exporteren**. Een bestaand pakket kunt u nog één keer toepassen: kies het bestand in het
SQLite-blok, vul de wachtwoordzin in en vink aan dat de configuratie van het platform wordt
vervangen. Het pakket wordt omgezet in een document dat de configuratie van het platform
vervangt, en dat document wordt net als elk ander gepland en toegepast, met uw rechten per
wijziging. Wat in gebruik is, wordt nooit verwijderd: een team met afspraken, een actieve
sluiting, een geaccepteerd tekort of API-toegang laat de hele import weigeren, met de reden op
het scherm, en er wordt niets geschreven. Het pakket moet van dezelfde versie van OpenPlanner
zijn. Medewerkers en het auditlog blijven. Na de import gelden de instellingen binnen tien
seconden. De bootstrap-sleutels van de server (`SETTINGS_ENCRYPTION_KEY` en de andere twee)
gaan nooit mee: elke installatie houdt haar eigen sleutels.

Elke toegepaste wijziging en elke import staat in het auditlog, zonder wachtwoordzin en zonder
waarden. U moet kort geleden zijn ingelogd. Het grootste toegestane bestand is de
platforminstelling *Maximale grootte van een configuratiepakket*.

**Via de API.** Een pipeline kan hetzelfde doen zonder de portal: een API-client met het
recht *Configuratiepakket exporteren en importeren* (`configuration:transfer`, te geven op
**API-koppelingen**) haalt het pakket op en zet het neer met `GET` en `PUT
/api/agent/v1/configuration`, de wachtwoordzin in een header. Dat recht geldt voor de hele
installatie, ongeacht de organisatie en de teams van de client; geef het alleen aan een
client voor het inrichten van installaties, nooit aan een bot. Dezelfde regels en hetzelfde
auditlog gelden, met de client-id als actor.

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
