# Redactiestatuut rolhandleidingen

> **Eerst invullen per organisatie:** het woord voor wie boekt (`{{participants}}` in
> `variables.json`), de aanspreekvorm per rol, wat niet genoemd wordt (§2.3), of de productnaam
> in beeld mag, en de echte datum en versie in de front matter.

Dit statuut legt vast hoe de vier rolhandleidingen worden geschreven, opgebouwd en gecontroleerd.
Het geldt voor elke schrijver, mens of model. Wie ervan afwijkt, zegt waarom.

## 1. Wat we maken

Vier handleidingen, elk voor één rol, in het Nederlands:

| Bestand | Rol | Lezer | Vorm | Omvang (A4, met afbeeldingen) | Taal | Aanspreekvorm |
|---|---|---|---|---|---|---|
| `sources/10-beheerder.md` | Beheerder | organisatiebeheerder (functioneel beheer) en applicatiebeheerder (technisch-functioneel beheer) | naslag, inrichtingschecklist, beheerkalender, walkthroughs | 20–30 | B2 | je |
| `sources/20-planner.md` | Planner | wie de bezetting en de afspraken van een groep regelt | werkinstructies, weekritme, walkthroughs | 12–20 | B2 | je |
| `sources/30-medewerker.md` | Medewerker | wie de videogesprekken voert | werkkaart (1 A4), walkthrough, "wat als" | 6–10 | B1 | je |
| `sources/40-deelnemers.md` | {{Participants}} | wie een videoafspraak maakt | "zo werkt het", walkthrough, vragen en antwoorden | 4–6 | B1 | u |

De handleidingen zijn ook bewijs dat de software bestaat en werkt. Daarom komt elke
schermafdruk uit de echte, draaiende software (zie §6) en staat er niets in wat niet gebouwd is.

## 2. Vaste regels

1. **Alleen wat gebouwd is.** Bronnen, in deze volgorde: de schermteksten
   (`app/i18n/locales/nl.po`), de handleiding per scherm (`docs/manual/nl/*.md`), de
   rechtenmatrix (`docs/authorization.md`) en de beheerstructuur (`docs/admin-map.md`). Staat
   iets alleen in een ADR, dan is het niet gebouwd: laat het weg. Twijfel je, laat het weg en
   noem het in je rapport.
2. **Geen productnaam.** Schrijf `{{product}}` waar de naam van het systeem staat, en
   `{{Product}}` aan het begin van een zin. De bouwstraat maakt daar nu "de afsprakenplanner"
   van; later komt de echte naam er met één wijziging in. Het beheergedeelte heet
   "het beheerportaal".
3. **Niet noemen:** [per organisatie invullen: koppelingen of functies die (nog) niet getoond
   worden, bijvoorbeeld een contactcenterkoppeling, API-clients, het configuratiepakket of de
   diagnosemodus]. Kies ook hoe een groep afspraken doorzet: met de **eigen wachtrij** (in het
   scherm: "Eigen wachtrij van …") of met een contactcenter, en schrijf alleen over die keuze.
4. **Wel noemen:** Webex Meetings, voor het videogesprek.
5. **Eindgebruikers** heten "{{participants}}". In de schermen heten zij **deelnemer**.
   Leg dat verband één keer uit in elke handleiding voor medewerkers, planners en beheerders,
   en gebruik daarna het schermwoord "deelnemer" waar je een scherm beschrijft.
6. **Geen klantnaam, geen sectorwoorden** buiten "{{participants}}". Voorbeelden gebruiken
   `voorbeeld.nl` en verzonnen namen (Sanne Jansen, Ahmed el Amrani, Bakkerij De Korenaar).
7. **Tijdloos.** Geen datums, jaartallen of "binnenkort" in de lopende tekst. Versie en datum
   staan alleen in de kop van het bestand (front matter) en zijn de echte. Nooit een eerdere
   datum dan de werkelijke.
8. **Schermteksten letterlijk en vet:** **Volgende oppakken**. Navigatie als pad:
   **Groepen → [groep] → Planning → Bezetting**. Controleer elke schermtekst in `nl.po`.
9. **Getallen uit de software** (standaardwaarden, grenzen) alleen als ze in de bronnen staan,
   en dan met "standaard" erbij als de organisatie ze kan wijzigen.
10. **Privacy:** zeg "verwijderd": een verwijdering haalt velden weg (ADR 0102). Zeg welke gegevens bewaard worden en
    wanneer ze verdwijnen, zoals de bronnen het zeggen.

## 3. Schrijfwijze

- **Minimalisme (taakgericht).** Begin met wat de lezer wil doen. Eén handeling per stap. Het
  resultaat staat na de stappen. Help fouten herkennen en herstellen ("Wat als …").
- **Korte zinnen.** B1: hoogstens 15 woorden per zin waar het kan, actieve vorm, gewone woorden,
  een vakwoord pas na uitleg. B2: hoogstens 20 woorden.
- **De lezer doet het, niet het systeem.** "Kies **Opslaan**", niet "Er dient te worden opgeslagen".
- **Waarom bij elke taak.** Eén zin: wanneer en waarom doe je dit.
- **Geen marketing.** Geen "eenvoudig", "snel", "gebruiksvriendelijk". Laat de stappen dat tonen.
- **Woorden van het scherm.** Gebruik "groep" (zoals het menu **Groepen**); leg één keer uit dat een
  groep een team is met een eigen agenda en een eigen pagina voor {{participants}}.

## 4. Vaste opbouw

Elk bestand begint met front matter en volgt deze volgorde. Laat een onderdeel alleen weg als het
voor de rol niet bestaat.

```markdown
---
title: Handleiding voor de planner
subtitle: Bezetting en afspraken van een groep regelen in {{product}}
role: Planner
version: "0.1 (concept)"
date: [echte datum]
software_version: "{{software_version}}"
---

# Handleiding voor de planner

## In het kort
(3–6 leerdoelen: "Na deze handleiding kun je …")

## Jouw rol
(wat je doet, wat je wel en niet kunt, met wie je samenwerkt)

## Voordat je begint
(inloggen, wat je nodig hebt)

## Walkthrough: <scenario>
(genummerde stappen met schermafdrukken)

## Taken
### <taak>
**Wanneer:** …
1. …
**Resultaat:** …
> **Let op:** …

## Wat als …
## Begrippen
## Hulp en contact
```

## 5. Markdown die de bouwstraat ondersteunt

- Koppen `#`, `##`, `###`; niet dieper.
- Genummerde stappen `1.`, opsommingen `-`; één niveau inspringen mag.
- Tabellen met een kopregel; geen uitlijning (`|:---|`).
- **Afbeelding:** op een eigen regel, `![alt-tekst](img/<id>.png "Bijschrift")`. De alt-tekst
  zegt wat je ziet en waarom het ertoe doet (voor wie het beeld niet ziet). Het bijschrift wordt
  "Afbeelding n. …". Gebruik alleen id's uit `shot-list.md`.
- **Kaders:** een citaatblok dat begint met een vet label: `> **Tip:** …`, `> **Let op:** …`,
  `> **Belangrijk:** …`.
- **Nieuwe pagina:** een regel `<!-- pagebreak -->`.
- **Werkkaart** (alleen medewerker): kop `## Werkkaart`, direct na de titel, past op één A4.
- Variabelen: `{{product}}`, `{{Product}}`, `{{help_desk}}`, `{{software_version}}`,
  `{{participants}}`, `{{Participants}}` (uit `variables.json`).
- **Taakkoppeling:** op de regel direct vóór de kop van elke walkthrough en elke taak:
  `<!-- task: screen=<scherm>; permission=<recht>; shots=<id>,<id> -->`. `screen` is het
  hulponderwerp van het scherm (bestandsnaam in `docs/manual/nl/` zonder `.md`) of een
  deelnemerspagina (`participant:group-page`, `participant:booking-form`, `participant:confirm`,
  `participant:manage`, `participant:move`, `participant:cancel`, `participant:join`,
  `participant:trial`, `participant:email`); `permission` is een code uit
  `docs/authorization.md`; `shots` zijn id's uit de opnamelijst. Hiermee controleert en
  bijwerkt de bouwstraat de handleiding automatisch, en later toont de software de taak op het
  juiste scherm.
- Geen HTML behalve `<!-- pagebreak -->` en de taakkoppelingen.

## 6. Schermafdrukken en walkthroughs (bewijs)

- Alleen schermafdrukken van de echte software: een lokale installatie gebouwd uit de broncode,
  gevuld met verzonnen gegevens. Nooit een mock-up of een bewerkte schermafdruk.
- Toegestaan: bijsnijden en het zichtbaar afdekken (grijze balk) van de productnaam als die
  verborgen blijft, van de termen die niet genoemd worden (§2.3) en van versienummers. Elke
  afdekking staat in `evidence/log.md`.
- Wat zonder Webex Meetings niet te tonen is (deelnemen aan het gesprek, proefgesprek), wordt
  opgenomen in een testomgeving met Webex Meetings. Tot dan staat er een kader
  "Schermafdruk volgt", nooit een nagemaakt beeld.
- `evidence/log.md` noemt per opname: de commit van de software, het tijdstip, de rol waarmee
  is ingelogd en het adres.
- **Voorbeeldgegevens.** Agenda's, wachtrij, rapporten en auditlog worden gevuld met verzonnen
  personen, via dezelfde echte gebruikerspaden (`sample-data.mjs`). Hun afspraken liggen op de
  werkdagen na de opnamedatum; het boeken, bevestigen, verplaatsen en annuleren gebeurde op de
  opnamedag zelf. Nooit een eerdere datum en nooit een verzette klok: wat in beeld staat,
  gebeurde echt op die dag.
- **Versienummers en commit-id's** worden in beeld afgedekt, zodat schermafdrukken niet verouderen.
  De echte softwareversie en de echte datum staan op de titelpagina en in het bewijslog.

## 7. Controle voor oplevering

1. Feitencheck: elke schermtekst staat in `nl.po`, elke regel in de bronnen van §2.
2. Lezerstest: een lezer zonder voorkennis voert met alleen de handleiding drie taken uit.
3. Alle afbeeldingen hebben een alt-tekst; geen kleur als enige signaal.
4. Niets uit de lijst "Niet noemen" (§2.3, `do_not_name`) in tekst of beeld, en geen productnaam
   als die verborgen blijft (`hide_product_name`).
5. `check.py` meldt 0 blokkerende bevindingen.
