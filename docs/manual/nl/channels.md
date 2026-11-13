# Kanalen

Op dit scherm legt de superuser per organisatie vast hoe berichten aan deelnemers de deur uit
gaan: de mailserver (SMTP) voor e-mail en de sms-gateway voor sms. U opent het via
**Organisaties → organisatie → Deelnemers → Kanalen**. Wachtwoorden en API-sleutels zijn alleen te schrijven:
het veld is altijd leeg, en een leeg veld laat het opgeslagen geheim staan (ADR 0022).

## E-mail

Vul de mailserver van de organisatie in: host, poort, beveiliging (STARTTLS op 587, TLS op 465)
en, als de server dat vraagt, een gebruikersnaam met wachtwoord. Een login zonder STARTTLS of
TLS weigert het portaal: het wachtwoord zou dan leesbaar over de lijn gaan. In productie weigert het portaal
ook een server zonder STARTTLS of TLS (*geen*): de e-mail bevat namen, adressen en deelnamelinks.
Alleen de beheerder van de server kan een uitzondering toestaan, met de hostinstelling
`SMTP_PLAINTEXT_RELAY_ALLOWED`; een oudere server van die soort staat als fout op het overzicht tot
hij is aangepast.

Het **afzenderadres** is wat deelnemers in hun postvak zien. Gebruik een adres van het domein van
de organisatie, bijvoorbeeld `afspraken@acme.nl`, met een **afzendernaam** zoals
"Acme Demo, afspraken". Het **antwoordadres** is waar een antwoord van een deelnemer
terechtkomt; laat het leeg als het afzenderadres zelf bemenst is. De **DKIM-selector** is
alleen nodig voor de controle hieronder; vraag hem aan uw mailbeheerder.

Zonder eigen instellingen gaat de e-mail van de organisatie via de mailserver van de
installatie, als de serverbeheerder die heeft ingesteld.

## Testmail

Een testmail gaat door precies de server die het systeem voor deze organisatie gebruikt. Slaagt
hij, dan accepteert de server de mail; dat zegt nog niets over het postvak van de deelnemer. Mislukt
hij, dan staat erbij waarom, in eigen woorden en zonder het antwoord van de server.

## Afzenderdomein

Of een e-mail in het postvak of in de spammap belandt, beslissen ontvangers (Gmail, Outlook, de
Nederlandse providers) vooral op de DNS-records van het domein in het afzenderadres. De knop
**Afzenderdomein controleren** leest die records en geeft per record een oordeel:

| Record | Wat het is | In orde als |
|---|---|---|
| SPF | Welke servers namens het domein mogen mailen | Er is één record dat eindigt op `-all` of `~all` |
| DKIM | De handtekening die de mailserver op elke mail zet, met de publieke sleutel in DNS | De sleutel van de ingevulde selector is gevonden |
| DMARC | Wat een ontvanger doet als SPF en DKIM niet kloppen, en waar hij dat meldt | Het beleid is `quarantine` of `reject` |

De oordelen betekenen:

- **In orde**: het record is er en is goed opgebouwd.
- **Aandachtspunt**: het record is er, maar beschermt niet. Bij SPF eindigt het op `+all` of
  `?all`; bij DMARC staat het beleid op `none` (alleen rapporteren).
- **Ontbreekt** of **Ongeldig**: laat uw mailbeheerder het record toevoegen of nakijken.
- **Niet vast te stellen**: de DNS-opzoeking is mislukt. Probeer het later opnieuw; blijft het
  zo, waarschuw dan de serverbeheerder.
- **Niet gecontroleerd**: alleen bij DKIM, zolang de selector niet is ingevuld.

Voor Nederlandse organisatiedomeinen zijn SPF, DKIM en DMARC met `p=reject` verplicht (Forum
Standaardisatie, "pas toe of leg uit"). De onafhankelijke test daarvoor is **internet.nl**;
gebruik die als sluitstuk.

### Wat de controle niet ziet

De controle leest alleen DNS. Zij ziet niet of uw mailserver de mail ook echt ondertekent, en
met welk domein. Dat is de valkuil: DMARC slaagt alleen als het domein waarmee de server
ondertekent (of het domein dat SPF controleert) gelijk is aan het domein van het
afzenderadres. Verstuurt u `afspraken@acme.nl` via een provider die ondertekent voor
`provider.example`, dan belandt de mail in spam, hoe goed de records ook zijn. Vraag uw
mailbeheerder dus om twee dingen: dat de server met het domein van het afzenderadres
ondertekent, en welke selector hij daarvoor gebruikt.

Verder helpt: een vaste afzender per organisatie (niet wisselen), korte en voorspelbare
onderwerpregels, en bij problemen de DMARC-rapporten laten uitlezen door de mailbeheerder.

## Sms

Kies de provider, vul de API-host en de API-sleutel in, en de afzender die deelnemers zien: een
naam van ten hoogste elf tekens of een geregistreerd nummer. De landcode geldt voor nummers die
zonder landcode zijn ingevuld.

## Contactcenter

Alleen de superuser stelt dit in. Een organisatie kan haar eigen contactcenter koppelen, ook als
dat een andere omgeving (tenant) is dan die van de installatie. Kies het **Platform**, vul het
**API-adres** van uw regio in (alleen https), de **Client-ID** van de Service App en het
**Clientgeheim**. Het **Refresh-token** is het token dat de Service App kreeg toen de beheerder
van uw omgeving haar toestemming gaf. De geheimen worden versleuteld bewaard en nooit meer
getoond; een leeg veld laat het bewaarde geheim staan.

Na het opslaan ziet u het **Webhookadres** van deze organisatie. Het contactcenter meldt daar de
status van taken. OpenPlanner controleert elke melding met een geheim dat de server zelf maakt;
niemand typt of ziet het. Met **Nieuw webhookgeheim maken** vervangt u het, bijvoorbeeld als u
denkt dat het is uitgelekt; het oude werkt dan meteen niet meer. **Eigen koppeling verwijderen**
wist de koppeling en haar geheimen.

Zolang een organisatie geen eigen koppeling heeft, gebruikt zij de koppeling van de installatie.
Die terugval verdwijnt in een volgende versie. Met een eigen koppeling gaan de taken van deze
organisatie alleen via die koppeling; ontbreekt het clientgeheim of het refresh-token, dan wachten
ze tot de koppeling compleet is. Een API-adres op deze server of in een intern netwerk wordt
geweigerd. Bij het opslaan meldt OpenPlanner het webhookadres met het geheim aan bij het contactcenter,
en opnieuw na **Nieuw webhookgeheim maken**. Lukt dat niet, dan zegt de melding bovenaan het
scherm dat; controleer de rechten van de Service App (cjp:config_write) en maak daarna een
nieuw webhookgeheim. Koppel of ontkoppel een organisatie als er geen taken open staan:
een taak die via de vorige koppeling is gemaakt, wordt niet meer via de nieuwe afgesloten.

<!-- field: cc_inbound_enabled -->
### Gebeurtenissen van het platform verwerken (pauze)

Staat standaard aan. Zet het uit om het webhookadres van deze organisatie te **pauzeren**, bijvoorbeeld
terwijl u de flows van het platform wijzigt. Een gepauzeerd adres controleert elke aanroep nog net
als voorheen (netwerk, handtekening, tijd): een aanroep die geweigerd zou worden, wordt geweigerd.
Een aanroep die klopt krijgt het gebruikelijke `204`, zodat het platform zijn aanmelding houdt en
die niet uitzet, maar de gebeurtenis wordt weggegooid: er wordt niets bij de boeking vastgelegd en
niets onthouden, dus de herbezorging door het platform nadat u hervat is, wordt wel verwerkt. De
kaart toont een melding *Gepauzeerd* zolang het duurt. Pauzeren of hervatten verandert het
webhookgeheim en de aanmelding bij het platform niet.

Hervatten speelt wat is weggegooid niet opnieuw af. De toestanden van de open taken worden
bijgewerkt door de periodieke controle van de taken (die op een schema draait en niet vanaf de kaart
te starten is), dus een toestand kan even duren voor hij is ingehaald.

<!-- field: cc_allowed_networks -->
### Toegestane bronnetwerken

De netwerken waarvandaan de webhooks van het contactcenter naar het adres van deze organisatie
mogen komen, één per regel in CIDR-notatie (bijvoorbeeld `203.0.113.0/24`; een enkel adres mag ook),
hooguit 32. Vraag de leverancier van het contactcenter om zijn actuele reeksen. De lijst **vervangt**
voor deze koppeling de standaardlijst van de installatie; leeg betekent dat de standaardlijst van de
installatie geldt, en zonder standaardlijst mag elke bron aanroepen.

De kaart zegt altijd in welke van drie standen het adres staat: *niet beperkt* (elke bron mag
aanroepen; de handtekening beslist nog steeds), *beperkt tot N netwerken* (uit de eigen lijst van
deze koppeling of de standaardlijst van de installatie) of *een lijst is verplicht en ontbreekt* (de
host eist een lijst, dus het adres accepteert geen enkele aanroep tot er een is). Een lijst is een
extra laag, niet het slot: de reeksen van de leverancier worden door al zijn klanten gedeeld, dus
elke aanroep uit de lijst moet nog steeds een juiste handtekening dragen, en een verouderde lijst
weigert de leverancier zelf. Houd de lijst bij als de leverancier nieuwe reeksen aankondigt.

Een lijst kan alleen worden beoordeeld zolang de proxy voor OpenPlanner het adres van de aanroeper
goed doorgeeft. Als het proxyvertrouwen `FORWARDED_ALLOW_IPS` `*` is of breder dan één host, zegt de
kaart dat in rood en worden aanroepen naar dit adres geweigerd tot de host het herstelt. Een
enkele aanroep waarvan het adres niet te vertrouwen is (doorgestuurde headers die zijn peer niet
mocht sturen) wordt op zichzelf geweigerd; het herstel
staat in het runbook, *Webhooks refused for their network*. Alleen de lijst opslaan verandert het
webhookgeheim en de aanmelding bij het platform niet.

Drie opstellingen kunnen geen lijst gebruiken: een proxy die door een heel netwerk wordt vertrouwd
in plaats van door één adres (een Kubernetes-ingress, waarvan de pods wisselen), een content
delivery network vóór de proxy (de aanroeper wordt het adres daarvan, en dat in de lijst zetten laat
al zijn klanten toe) en een leverancier die een publieke naam vanaf een privéadres aanroept. Laat de
lijst dan leeg en vertrouw op de handtekening, of vraag het aan uw platformbeheerder.

Een netwerk dat een hele adresfamilie dekt (`0.0.0.0/0` of `::/0`, of een IPv6-netwerk als `::/1`
dat ook elk als IPv6 geschreven IPv4-adres bevat) wordt geweigerd: dat is geen lijst. Is de opgeslagen lijst niet te lezen, dan zegt de kaart dat en accepteert het adres geen
enkele aanroep tot u de lijst opnieuw opslaat.

**Stuur nadat u een lijst hebt ingesteld een testbezorging** en controleer dat die wordt
geaccepteerd. De kaart toont nog geen geweigerde aanroepen, dus een lijst die de leverancier buiten
sluit ziet u hier niet.

## Wat wordt vastgelegd

Elke wijziging komt in het auditlog met de namen van de gewijzigde velden, nooit de waarden.
Van een testmail staat het resultaat in het log, niet het adres; van een domeincontrole het
oordeel, niet de records.

## Meldingen aan beheerders

Waar OpenPlanner meldingen heen stuurt als er iets misgaat met de koppelingen van deze
organisatie of de taken van haar groepen: een e-mailadres van de beheerders of een Webex-ruimte.
Per adres kiest u **Vanaf**: alle waarschuwingen en storingen, of alleen storingen. Hoogstens tien
adressen. Met **Testmelding sturen** controleert u een adres meteen.

Een melding die blijft, komt na het herhaalinterval opnieuw (instelling *Melding opnieuw sturen
na*, standaard vier uur); tussendoor telt OpenPlanner alleen, en het volgende bericht noemt hoe
vaak. Lukt het versturen niet, dan probeert OpenPlanner het na 1, 5 en 15 minuten opnieuw. Een
bericht bevat alleen codes, de organisatie, de groep, het tijdstip en een link naar het portaal:
nooit gegevens van deelnemers.

**E-mail** gaat via het mailkanaal hierboven (of het reservekanaal van het platform).

**Webex-ruimte**: maak in het Webex-ontwikkelaarsportaal een bot aan (alleen het recht
`spark:messages_write`), voeg de bot toe aan de ruimte, plak het token van de bot bij
**Webex-bot** en vul het id van de ruimte in als adres. Het token wordt versleuteld bewaard en
nooit meer getoond. Stap voor stap, met de terugval van een groep naar de eigen wachtrij: zie
[Contactcenter inrichten](contact-center-setup.md), stap 10 en 11.
