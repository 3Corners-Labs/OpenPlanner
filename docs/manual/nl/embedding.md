# Insluiten op websites

Een groep heeft altijd een eigen pagina die u vanaf uw website kunt linken. Wilt u de
afsprakenkalender liever direct in een pagina van uw eigen website tonen, dan kan dat met de
widget. Dit scherm bepaalt op welke websites dat mag. U opent het via **Insluiten** in het
menu. Organisatiebeheerders en de superuser kunnen het gebruiken, elk voor hun eigen
organisaties.

Twee dingen zijn nodig voordat een groep in de widget verschijnt:

1. De website staat hier in de lijst van de organisatie.
2. De groep zet **Insluiten op websites** aan in haar
   [groepsinstellingen](group-settings.md).

Staat de widget op een website die niet in de lijst staat, dan toont hij geen afspraken en kan
niemand er boeken. Zo werkt een gekopieerde widget op een andere website niet.

<!-- field: origins -->
## Websites, één per regel

Vul per regel het begin van het webadres in: het deel tot en met de domeinnaam, zoals
`https://www.gemeente.nl`. Zo herkent de browser uw website.

- Alleen adressen die met `https://` beginnen.
- Geen pad: `https://www.gemeente.nl/afspraak` wordt geweigerd. De widget werkt op elke
  pagina van een toegestane website.
- Geen sterretjes. Een subdomein is een eigen website: wilt u de widget op
  `https://werk.gemeente.nl` en op `https://www.gemeente.nl`, zet dan beide in de lijst.
- Een `/` aan het eind, hoofdletters of de standaardpoort `:443` mogen; ze worden weggelaten.
- Een lege regel wordt overgeslagen. Laat u alle regels leeg en slaat u op, dan staat er geen
  website meer in de lijst en werkt de widget nergens.

Op een test- of ontwikkelomgeving mag ook `http://localhost` met een poort, om de widget op
uw eigen computer te proberen. In productie wordt dat geweigerd.

Elke wijziging komt in de auditlog, met het aantal toegevoegde en verwijderde websites.

## Groepen

Onder de lijst ziet u per groep van de organisatie of die in de widget verschijnt. De naam
van de groep linkt naar haar instellingen, waar u **Insluiten op websites** aan- of uitzet.

## Code voor uw website

Onderaan staat de code die de webredactie in een pagina plakt: één regel met het script en
per groep die aan staat een element `<gov-booking>`. Het script hoeft maar één keer op een
pagina te staan, ook als u meer groepen toont.

Het script heeft een `integrity`-waarde. Daarmee controleert de browser dat het script niet
is veranderd. Na elke update van OpenPlanner verandert die waarde: neemt de webredactie de
code met `integrity` over, dan moet ze de nieuwe waarde na een update overnemen, anders laadt
de kalender niet meer. Wilt u dat niet, laat `integrity="…"` dan weg. De technische uitleg
voor de webredactie staat in `docs/widget.md`.
