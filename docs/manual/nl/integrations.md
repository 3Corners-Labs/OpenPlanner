# Koppelingen

Dit scherm toont de staat van de koppelingen met Webex (videomeetings) en Webex Contact Center
(WxCC) voor de hele installatie. U opent het met **Koppelingen** in het menu. Alleen de
superuser ziet het. Geheimen staan versleuteld opgeslagen en worden nooit getoond.

Het grootste deel van het scherm is een rapport. De enige knop is **Service App controleren**.
De andere controles werkt u bij op de opdrachtregel van de webcontainer; het scherm toont dan
hun laatste uitslag.

## Webex Service App

De kaart toont of de Service App verbonden is, tot wanneer het toegangstoken en het
verversingstoken geldig zijn, en of Webex Contact Center verbonden is. *Nog niet gebruikt*
betekent dat er nog geen token is opgevraagd.

Loopt het verversingstoken bijna af, dan staat bovenaan het scherm een waarschuwing met de
datum. Maak vóór die datum een nieuw token, zoals het runbook beschrijft; anders kunnen er
geen meetings en werkitems meer worden aangemaakt.

## Kanalen

Een tabel met één rij per organisatie toont of e-mail en sms **Ingesteld**, **Geheim ontbreekt** of
**Niet ingesteld** zijn. De naam van de organisatie leidt naar het scherm **Kanalen**, waar u ze
wijzigt (zie [Kanalen](channels.md)).

## Service App controleren

<!-- field: service_app_check -->
### Knop Service App controleren

De knop doet elke soort aanroep die OpenPlanner naar Webex en WxCC doet, zonder iets te
veranderen: WxCC weigert een lege taak en een onbekend taak-id, en dat is het verwachte
antwoord. Per aanroep toont de tabel de HTTP-status en een uitslag:

- **OK**: de aanroep slaagt.
- **Mislukt**: een verplichte aanroep mislukt. Lees de toelichting; bij een 401 of 403 staat
  daar de scope die waarschijnlijk ontbreekt. Zie het runbook (Service App).
- **Waarschuwing**: een aanroep die niet verplicht is, mislukt.

De uitslag blijft staan met het tijdstip van de laatste controle. De controle komt ook in de
auditlog, als geslaagd of mislukt. Ook als de onderhoudsmodus aanstaat kunt u hem gebruiken,
om een herstelde koppeling te testen. Is er nog niet gecontroleerd, dan toont het scherm het
alternatief op de opdrachtregel (`python -m app.cli wxcc-check`).

## Webhooks

De twee adressen die de meldingen van Webex en WxCC ontvangen, elk met de vermelding of er een
geheim is ingesteld. Een adres zonder geheim staat uit. Registreer de adressen bij de
platforms met `python -m app.cli register-webhooks`.

## Registraties bij Webex en WxCC

Een lijst van alle webhooks en abonnementen bij de platforms die naar OpenPlanner verwijzen,
met hun staat bij de laatste controle:

- **Actueel**: in orde.
- **Verouderd**: staat er nog, maar klopt niet meer. De reden staat onder de staat: een ander
  adres (een oude tunnel of basis-URL), geen hostfilter, een hostaccount dat geen groep
  gebruikt, een gebeurtenis die OpenPlanner niet gebruikt, inactief, of een dubbele.
- **Niet van OpenPlanner**: hoort bij een andere toepassing en blijft met rust.

Onder **Ontbreekt** staan registraties die er zouden moeten zijn maar er niet zijn. Dit scherm
werkt de lijst niet zelf bij: voer `python -m app.cli webhooks-list` uit. Verouderde
registraties ruimt u op met `python -m app.cli webhooks-reset` (runbook, hoofdstuk 6).

## Afleveringstest

Ondertekende testberichten via het webhookadres naar OpenPlanner zelf, met onbekende ids zodat
er geen afspraak verandert. Per adres en test zegt het resultaat of het bericht is afgeleverd
en gecontroleerd, geweigerd zoals het hoort (een verkeerde handtekening), of wat er misgaat:
een handtekening die afwijkt van het geheim van de webcontainer, controle die uitstaat, een
adres dat OpenPlanner niet bereikt, een fout in tunnel of proxy, of een adres dat niet
bereikbaar is. Bijwerken kan met `python -m app.cli webhooks-health`.

## Groepen

Een tabel van alle groepen met of video aanstaat en of werkitems naar WxCC gaan (met de
routeringswaarde). De naam van de groep leidt naar de instellingen, waar u dit wijzigt.
