# Auditlog

Het auditlog laat zien wat er in het beheerportaal is gedaan en wat het platform zelf heeft
gedaan. U opent het met **Auditlog** in het menu. U ziet alleen uw eigen niveau: een
groepsbeheerder de eigen groepen, een organisatiebeheerder en de rol Compliance en privacy ook
de eigen organisatie, de superuser alles.

Het log bevat nooit gegevens van burgers: geen namen, e-mailadressen of telefoonnummers.

## Wat een regel laat zien

- **Wanneer**: het tijdstip in de tijdzone van het portaal, met daaronder dezelfde tijd in UTC.
  Die UTC-tijd vindt u terug in exports en op het logplatform.
- **Wie**: de beheerder, *Systeem* voor wat het platform zelf deed, of *niet ingelogd*.
- **Wat**: de handeling, met wat er veranderd is (alleen de namen van velden, nooit waarden),
  de afspraak waar het om gaat en de link **Hele keten**.
- **Resultaat**: gelukt, geweigerd, mislukt, wordt opnieuw geprobeerd of overgeslagen, met
  de reden of foutcode.

## Filteren

- **Minimaal niveau**: bijvoorbeeld *Waarschuwing* toont waarschuwingen, fouten en kritieke
  meldingen. Geweigerd en opnieuw proberen zijn waarschuwingen, mislukt is een fout.
- **Soort**: *Beheeracties* (inloggen, wijzigingen, weigeringen), *Systeem* (berichten,
  videogesprekken, verwijderen van gegevens, opslag), *Beveiliging* (geweigerde webhooks,
  mislukte vernieuwing van het Webex-token) en *Diagnose*.
- **Van** en **Tot en met**: hele dagen in de tijdzone van het portaal.
- **Keten-id**: alle regels van één verzoek of één taak. U komt er ook met **Hele keten**
  bij een regel.
- **Groep**, **Afspraaknummer** en **Alleen problemen** werken zoals u verwacht.

Een onbekende of onjuiste waarde wordt genegeerd.

## Exporteren

Met **Exporteren als CSV** downloadt u precies wat het filter nu toont, binnen uw eigen
niveau. Organisatiebeheerders, de rol Compliance en privacy en de superuser kunnen exporteren;
groepsbeheerders niet.

- Het bestand opent in Excel met de goede tekens en kolommen (UTF-8, puntkomma).
- De kolommen zijn de velden van het standaard eventschema; tijden staan in UTC.
- Zonder periode krijgt u de laatste 366 dagen. Een langere periode of meer dan 100.000
  regels wordt geweigerd: maak het filter dan nauwer.
- Elke export komt zelf in het auditlog, met het aantal regels.

Een export bevat de identificatie (subject) van beheerders. Bewaar het bestand volgens het
beleid van uw organisatie.
