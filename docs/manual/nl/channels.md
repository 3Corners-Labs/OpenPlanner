# Kanalen

Op dit scherm legt de superuser per organisatie vast hoe berichten aan deelnemers de deur uit
gaan: de mailserver (SMTP) voor e-mail en de sms-gateway voor sms. U opent het via
**Organisaties → organisatie → Kanalen**. Wachtwoorden en API-sleutels zijn alleen te schrijven:
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
de organisatie, bijvoorbeeld `afspraken@gemeente.nl`, met een **afzendernaam** zoals
"Gemeente Demo, afspraken". Het **antwoordadres** is waar een antwoord van een deelnemer
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

Voor Nederlandse overheidsdomeinen zijn SPF, DKIM en DMARC met `p=reject` verplicht (Forum
Standaardisatie, "pas toe of leg uit"). De onafhankelijke test daarvoor is **internet.nl**;
gebruik die als sluitstuk.

### Wat de controle niet ziet

De controle leest alleen DNS. Zij ziet niet of uw mailserver de mail ook echt ondertekent, en
met welk domein. Dat is de valkuil: DMARC slaagt alleen als het domein waarmee de server
ondertekent (of het domein dat SPF controleert) gelijk is aan het domein van het
afzenderadres. Verstuurt u `afspraken@gemeente.nl` via een provider die ondertekent voor
`provider.example`, dan belandt de mail in spam, hoe goed de records ook zijn. Vraag uw
mailbeheerder dus om twee dingen: dat de server met het domein van het afzenderadres
ondertekent, en welke selector hij daarvoor gebruikt.

Verder helpt: een vaste afzender per organisatie (niet wisselen), korte en voorspelbare
onderwerpregels, en bij problemen de DMARC-rapporten laten uitlezen door de mailbeheerder.

## Sms

Kies de provider, vul de API-host en de API-sleutel in, en de afzender die deelnemers zien: een
naam van ten hoogste elf tekens of een geregistreerd nummer. De landcode geldt voor nummers die
zonder landcode zijn ingevuld.

## Wat wordt vastgelegd

Elke wijziging komt in het auditlog met de namen van de gewijzigde velden, nooit de waarden.
Van een testmail staat het resultaat in het log, niet het adres; van een domeincontrole het
oordeel, niet de records.
