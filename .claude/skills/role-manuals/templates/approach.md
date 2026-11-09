---
title: Aanpak adoptie, communicatie en training
subtitle: Zo voeren we {{product}} in bij beheerders, planners, medewerkers en {{participants}}
role: Projectleiding, adoptie en communicatie
version: "0.1 (concept)"
date: [echte datum]
software_version: "{{software_version}}"
---

# Aanpak adoptie, communicatie en training

## In het kort

Software is pas ingevoerd als mensen er anders door werken. Deze aanpak beschrijft hoe we dat
bereiken voor vier groepen: beheerders, planners, medewerkers, en de {{participants}} die een
videoafspraak maken. De aanpak heeft vijf stappen: **verkennen, ontwerpen, maken en toetsen,
uitrollen** en **borgen**.

De kern in zes zinnen:

- Elke rol krijgt eigen materiaal, opgebouwd rond **taken**, niet rond schermen.
- Elke rol krijgt de **vorm** die past bij hoe die rol werkt: een werkkaart voor de medewerker,
  werkinstructies voor de planner, naslag en checklists voor de beheerder, een korte uitleg voor
  {{participants}}.
- We schrijven alles vanuit de **werkende software**, met echte schermafdrukken. Zo is de
  handleiding ook het bewijs dat het product bestaat en doet wat we zeggen.
- Er is **één bron** voor al het materiaal. Daaruit maken we nu Word en PDF, en later HTML,
  zonder iets opnieuw te schrijven.
- We **meten** of het werkt en passen het materiaal aan bij elke nieuwe softwareversie.
- Elke taak is **gekoppeld** aan een scherm, een recht en zijn schermafdrukken. Daardoor kunnen we
  bij elke versie automatisch opnemen, controleren en bijwerken, en later de hulp in de software
  zelf tonen, op het juiste scherm en voor de juiste rol.

<!-- pagebreak -->

## 1. Uitgangspunten

| Uitgangspunt | Wat het betekent | Waarom |
|---|---|---|
| Rolgericht | Eén handleiding per rol, met alleen de taken van die rol | Lezers zoeken "hoe doe ik mijn werk", niet "wat doet dit scherm" |
| Taakgericht (minimalisme) | Begin met de handeling, één handeling per stap, help fouten herstellen | Mensen lezen pas als ze vastlopen; dan moet het antwoord direct vindbaar zijn |
| Twee lagen hulp | De ingebouwde hulp in het beheerportaal legt elk scherm uit; de rolhandleiding verbindt de schermen tot werk | Geen dubbel werk, wel een heel verhaal |
| Echt en herleidbaar | Alleen wat gebouwd is; elke schermafdruk komt uit de draaiende software en staat in het bewijslog | De handleiding dient ook als bewijs |
| Eén bron, meer vormen | Markdown als bron; Word en PDF nu, HTML later | Bijwerken op één plek; geen tegenstrijdige versies |
| White-label | De productnaam is één variabele; branding volgt later | De organisatie kiest haar eigen naam voor de dienst |
| Tijdloos | Geen datums in de lopende tekst; de versie hoort bij de softwareversie | Materiaal veroudert niet ongemerkt |
| Toegankelijk | B1 voor {{participants}} en medewerkers; alt-tekst bij elke afbeelding; koppen in volgorde | Iedereen moet het materiaal kunnen gebruiken, ook met een schermlezer |

## 2. Het verandermodel: ADKAR per rol

We gebruiken het ADKAR-model. Een mens verandert in vijf stappen: **bewustzijn** (waarom
verandert er iets), **bereidheid** (wat levert het mij op), **kennis** (wat moet ik weten),
**kunnen** (heb ik het geoefend) en **borgen** (blijft het zo). Een handleiding helpt bij kennis en
kunnen. Communicatie zorgt voor bewustzijn en bereidheid. Borgen vraagt meten en bijsturen. Wie
een stap overslaat, krijgt weerstand of terugval.

| Rol | Bewustzijn | Bereidheid | Kennis | Kunnen | Borgen |
|---|---|---|---|---|---|
| Beheerder | Startbijeenkomst met het project: wat verandert er in inrichting en rechten | Zelf inrichten en zien wie wat mag, in één rechtenmatrix | Beheerdershandleiding, inrichtingschecklist, ingebouwde hulp | Inrichten in de testomgeving; één proefafspraak van boeken tot gesprek | Beheerkalender; controle van de handleiding bij elke update |
| Planner | Informatiesessie: bezetting is een aantal medewerkers, geen rooster met namen | Tekorten direct zien en zelf oplossen; {{participants}} kiezen zelf een nieuwe tijd | Plannershandleiding, kernbegrippen, weekritme | Praktijktraining met drie oefeningen in de testomgeving | Weekritme als checklist; overleg met de sleutelgebruiker |
| Medewerker | Korte demonstratie in het teamoverleg | De eerste vrije medewerker krijgt het gesprek; nooit twee collega's tegelijk in één gesprek | Werkkaart en korte handleiding | Oefengesprek met een collega als deelnemer | Ondersteuning op de werkvloer in de eerste weken; vragenlog |
| {{Participants}} | Tekst op de website op de plek waar zij hun vraag stellen | Niet reizen, zelf een tijd kiezen, zelf verplaatsen of annuleren | De pagina's leggen zichzelf uit; korte uitleg "Een videoafspraak maken" | Het proefgesprek, als de organisatie het aanbiedt; andere contactmogelijkheden als het niet lukt | Vragen en antwoorden bijwerken met echte vragen |

<!-- pagebreak -->

## 3. Vorm per rol

Elke rol gebruikt materiaal op een andere manier. We kiezen de vorm met het Diátaxis-model. Dat
onderscheidt vier soorten documentatie: **leren** (een begeleide doorloop, de walkthrough),
**doen** (een werkinstructie voor één taak), **opzoeken** (naslag) en **begrijpen** (uitleg van
begrippen en samenhang). Per rol leggen we het accent anders.

| Rol | Wat de lezer nodig heeft | Vorm | Omvang | Taal | Nu | Later |
|---|---|---|---|---|---|---|
| Beheerder | Alles kunnen inrichten en controleren; weten wie wat mag | Opzoeken en doen: naslag, inrichtingschecklist, beheerkalender, twee walkthroughs | 20–30 blz. | B2, je-vorm | Word en PDF | HTML op het intranet, naast de ingebouwde hulp |
| Planner | Elke week de bezetting goed zetten en afwijkingen oplossen | Doen en begrijpen: werkinstructies, kernbegrippen, weekritme, drie walkthroughs | 12–20 blz. | B2, je-vorm | Word en PDF | HTML op het intranet |
| Medewerker | Snel en zeker een gesprek oppakken en voeren | Doen: werkkaart van één A4, één walkthrough, "wat als" | 6–10 blz. | B1, je-vorm | Werkkaart geprint naast het scherm, PDF | HTML en de ingebouwde hulp |
| {{Participants}} | Een afspraak maken, wijzigen en het gesprek in komen | Leren en doen in één: walkthrough, vragen en antwoorden | 4–6 blz. | B1, u-vorm | PDF als download | HTML op de website, bij de link naar de afsprakenpagina |

Elke handleiding heeft dezelfde vaste opbouw. Wie er één kent, vindt zijn weg in alle vier:

1. **In het kort**: wat je na het lezen kunt (de leerdoelen).
2. **Jouw rol**: wat je doet, wat je wel en niet kunt, met wie je samenwerkt.
3. **Voordat je begint**: inloggen en wat je nodig hebt.
4. **Walkthroughs**: een volledig scenario, stap voor stap, met schermafdrukken.
5. **Taken**: korte werkinstructies (wanneer, stappen, resultaat, let op).
6. **Wat als …**: veelvoorkomende problemen en hoe je ze oplost.
7. **Begrippen** en **Hulp en contact**.

> **Tip:** De vaste regels voor taal, opbouw, schermafdrukken en wat we niet noemen staan in het
> redactiestatuut. Elke schrijver, mens of model, werkt daarmee.

## 4. De aanvliegroute in vijf stappen

De planning rekent terug vanaf **L**, de dag waarop de eerste groep live gaat. We gaan groep voor
groep live. Zo leert elke volgende groep van de vorige.

### Stap 1. Verkennen (L – 8 tot L – 6 weken)

**Doel:** weten voor wie we het doen en wat er voor hen verandert.

1. Maak een **stakeholderkaart**: wie merkt de verandering, hoe sterk, en wie heeft invloed.
2. Doe per rol een **doelgroepanalyse**: taken, hoe vaak, waar en op welk scherm, digitale
   vaardigheid, huidige werkwijze, zorgen.
3. Beschrijf per rol de **veranderimpact**: wat verandert, wat blijft, wat verdwijnt.
4. Wijs per rol een of twee **sleutelgebruikers** aan. Zij toetsen het materiaal, trainen mee en
   zijn in de eerste weken het aanspreekpunt op de werkvloer.
5. Leg de **succescriteria** vast (zie stap 5).

**Resultaat:** doelgroepanalyse, impactanalyse, sleutelgebruikers, meetplan.

### Stap 2. Ontwerpen (L – 6 tot L – 5 weken)

**Doel:** vastleggen wat iedereen na de invoering kan, en in welke vorm we dat aanbieden.

1. Schrijf per rol **leerdoelen** die je kunt toetsen: "De planner kan een tekort oplossen door
   een afspraak te verplaatsen of de deelnemer om een nieuwe tijd te vragen."
2. Kies per rol de **vorm** (hoofdstuk 3) en leg het **redactiestatuut** vast.
3. Maak de **opnamelijst**: elke schermafdruk met een vaste naam, rol en toestand.
4. Maak het **communicatieplan** (hoofdstuk 5) en het **trainingsplan** (hoofdstuk 6).

**Resultaat:** leerdoelen, redactiestatuut, opnamelijst, communicatie- en trainingsplan.

### Stap 3. Maken en toetsen (L – 5 tot L – 3 weken)

**Doel:** materiaal dat klopt en dat werkt voor de lezer.

1. **Schrijf vanuit de werkende software**, niet vanuit wensen of ontwerpdocumenten. Wat niet
   gebouwd is, staat er niet in.
2. **Maak de schermafdrukken** op een echte installatie met verzonnen gegevens. Wat
   Webex Meetings nodig heeft (deelnemen aan het gesprek, het proefgesprek), nemen we op in de
   testomgeving met Webex Meetings.
3. **Feitencheck:** elke schermtekst en elke regel komt overeen met de software.
4. **Inhoudelijke review** door de sleutelgebruikers van elke rol.
5. **Lezerstest:** drie tot vijf mensen per rol voeren drie taken uit met alleen de handleiding.
   Zij denken hardop; wij kijken waar ze vastlopen en passen de tekst aan. Een eerste ronde kan
   een taalmodel zonder voorkennis doen; de echte ronde doen mensen.
6. **Toegankelijkheid** van het materiaal zelf: koppen, alt-teksten, contrast, leesbare PDF.

**Resultaat:** de vier handleidingen versie 1.0 in Word en PDF, de werkkaart, vragen en
antwoorden, teksten voor de website.

### Stap 4. Uitrollen (L – 3 tot L + 2 weken)

**Doel:** iedereen kan op de eerste dag zijn werk doen, en weet waar hij hulp vindt.

1. **Train de trainer:** de sleutelgebruikers krijgen de training eerst en geven hem daarna mee.
2. **Trainingen per rol** volgens het trainingsplan, in de testomgeving.
3. **Communicatie** volgens het communicatieplan, met de leidinggevende als afzender van het
   waarom.
4. **Livegang per groep**, met ondersteuning op de werkvloer: de sleutelgebruiker is zichtbaar
   aanwezig, er is een kort dagelijks overleg in de eerste week, en elke vraag komt in het
   vragenlog.

**Resultaat:** getrainde gebruikers, een gevuld vragenlog, bijgewerkte vragen en antwoorden.

### Stap 5. Borgen (vanaf L + 2 weken)

**Doel:** de nieuwe werkwijze blijft, en het materiaal blijft kloppen.

1. **Meten** met de indicatoren hieronder, elke twee weken in de eerste twee maanden en daarna
   elk kwartaal.
2. **Feedbacklus:** vraag → vragenlog → vragen en antwoorden → handleiding.
3. **Bij elke softwareversie:** lees de wijzigingen, pas de handleiding en de schermafdrukken
   aan, en verhoog de versie van het materiaal.
4. **Nieuwe collega's:** de werkkaart en de handleiding van de rol zijn hun startpakket; de
   sleutelgebruiker is hun maatje in de eerste week.

| Indicator | Bron | Rol |
|---|---|---|
| Boekingen per dag | Rapport **Gebruik** van de groep | Planner, beheerder |
| Percentage bevestigd na boeken | Rapport **Gebruik**, tabel bevestiging per e-mail | Beheerder |
| Annuleringen en verplaatsingen, en door wie | Rapport **Gebruik** | Planner |
| Hoe ver vooruit er geboekt wordt | Rapport **Gebruik** | Planner |
| Langste wachttijd in de wachtrij, tegen het doel | Widget **Wachtrij in mijn groepen** op het overzicht (actueel, geen historie) | Planner, medewerker |
| Tekorten in de komende 14 dagen | Overzicht van de groep en de planningspagina's | Planner |
| Vragen per honderd afspraken | Vragenlog van de servicedesk | Adoptieregisseur |
| Tevredenheid van {{participants}} | Korte vraag na het gesprek, buiten de software | Adoptieregisseur |
| Tijd tot een nieuwe medewerker zelfstandig werkt | Sleutelgebruiker | Teamleider |

> **Let op:** De software telt nog niet of een gesprek is afgerond of dat iemand niet verscheen, en
> houdt geen historie van wachttijden bij. Die cijfers komen voorlopig uit het vragenlog en uit
> waarnemingen in de eerste weken.

<!-- pagebreak -->

## 5. Communicatieplan

Eerst het waarom, dan het wat, dan het hoe. Het waarom komt van de leidinggevende; het hoe van het
project en de sleutelgebruikers. Voor {{participants}} zijn de website en de berichten die de
software zelf stuurt de belangrijke kanalen; de beheerder past die berichten aan in het
beheerportaal.

| Moment | Doelgroep | Boodschap | Kanaal | Afzender |
|---|---|---|---|---|
| L – 6 weken | Beheerders, planners, teamleiders | Waarom we overstappen, wat het oplevert, hoe de invoering loopt | Startbijeenkomst | Sponsor (leidinggevende) |
| L – 5 weken | Medewerkers | Wat er voor jou verandert en wanneer; wie je sleutelgebruiker is | Teamoverleg, korte demonstratie | Teamleider en sleutelgebruiker |
| L – 4 weken | Beheerders | Inrichtingschecklist, toegang tot de testomgeving | E-mail en werksessie | Project |
| L – 3 weken | Planners en medewerkers | Uitnodiging voor de training, met de handleiding vooraf | Agenda-uitnodiging | Project |
| L – 2 weken | {{Participants}} | Nieuwe manier om een videoafspraak te maken; tekst bij de link | Website | Communicatieafdeling |
| L – 1 week | Iedereen intern | Laatste praktische informatie: wanneer, waar hulp, wie de sleutelgebruiker is | Intranet, teamoverleg | Project |
| L | Iedereen intern | We zijn live; zo meld je een vraag of probleem | Intranet, werkvloer | Sponsor en project |
| L + 1 week | Iedereen intern | Wat we horen en wat we al hebben aangepast | Intranet, teamoverleg | Project |
| L + 4 weken | Sponsor en teamleiders | Eerste meting en besluit over de volgende groepen | Overleg | Adoptieregisseur |

**Kernboodschap per rol** (één zin, steeds dezelfde):

- **Beheerder:** je richt de organisatie en de groepen zelf in, en ziet op één plek wie wat mag.
- **Planner:** je zet de bezetting per week; tekorten zie je direct en los je zelf op.
- **Medewerker:** je pakt het volgende gesprek op uit de wachtrij en voert het in Webex Meetings.
- **{{Participants}}:** u kiest zelf een moment voor een videogesprek, en verplaatst of
  annuleert het zelf.

## 6. Trainingsplan

We trainen in de testomgeving, met verzonnen gegevens. Elke training eindigt met een oefening die
de deelnemer zelfstandig uitvoert; die oefening toetst de leerdoelen.

| Rol | Vorm | Duur | Groep | Oefeningen |
|---|---|---|---|---|
| Beheerder | Werksessie met de leverancier | 2 dagdelen | 2–4 | Een nieuwe groep inrichten; een medewerker toegang geven; een proefafspraak van boeken tot gesprek |
| Planner | Praktijktraining | 1 dagdeel | 4–8 | De week inplannen; een afwijkende dag; een tekort oplossen |
| Medewerker | Korte training en oefengesprek | 1 uur | 6–12 | Een gesprek oppakken, voeren en vrijgeven; wat als de deelnemer niet komt |
| Sleutelgebruiker | Train de trainer | 1 dagdeel extra | 2–6 | Een training geven aan een collega; een vraag in het vragenlog verwerken |
| {{Participants}} | Geen training | – | – | De pagina's leggen zichzelf uit; het proefgesprek helpt bij camera en microfoon |

Volgens het 70-20-10-principe leren mensen het meeste in het werk zelf (70), van collega's (20)
en het minste in een training (10). Daarom zijn de werkkaart, de sleutelgebruiker en de
ondersteuning op de werkvloer net zo belangrijk als de training.

## 7. Rollen in de invoering

| Rol | Taak |
|---|---|
| Sponsor | Leidinggevende die het waarom uitdraagt en knopen doorhakt |
| Adoptieregisseur | Houdt de aanpak, de planning, het vragenlog en de meting bij |
| Sleutelgebruikers | Per rol: toetsen het materiaal, trainen mee, helpen op de werkvloer |
| Trainer | Geeft de trainingen en begeleidt de lezerstest |
| Functioneel beheer | Richt de software in, beheert de testomgeving, verwerkt wijzigingen |
| Communicatieadviseur | Maakt de teksten voor intranet en website |
| Leverancier | Levert de software, de testomgeving en de schermafdrukken uit de echte software |

## 8. Kwaliteit, bewijs en automatisch bijwerken

- **Bronnen.** De handleidingen volgen de schermteksten, de ingebouwde hulp per scherm en de
  rechtenmatrix van de software. Wat alleen in een ontwerp staat, staat er niet in.
- **Schermafdrukken.** Ze komen uit een installatie die uit de broncode is gebouwd, met verzonnen
  gegevens. De productnaam is zichtbaar afgedekt, nooit vervangen. Het bewijslog noemt per
  schermafdruk de softwareversie, het tijdstip, de rol en het adres.
- **Versies.** De versie van het materiaal hoort bij een softwareversie en staat op de
  titelpagina, met de echte datum. Een wijzigingslog houdt bij wat er per versie veranderde.
- **Productie.** Eén bron in Markdown; de bouwstraat maakt er Word, PDF en HTML van. Branding (naam,
  logo, kleuren) komt er later met één aanpassing in.

### Automatisch bijwerken bij een nieuwe softwareversie

Elke taak in de handleidingen draagt een koppeling: het scherm waar de taak gebeurt, het recht
dat ervoor nodig is en de schermafdrukken die erbij horen. Daardoor stelt de bouwstraat zelf vast
wat een nieuwe versie raakt. Eén opdracht doet het hele rondje:

1. **Opnemen.** De nieuwe versie wordt lokaal gebouwd en gevuld met voorbeeldgegevens: verzonnen
   deelnemers boeken, verplaatsen en annuleren via de echte schermen, rond de opnamedatum. Per
   schermafdruk loopt een script daarna het echte gebruikerspad door. Versienummers en het jaartal
   worden in beeld afgedekt; de echte versie en datum staan op de titelpagina. De vorige opname
   blijft ernaast bewaard.
2. **Controleren.** Een controlescript vergelijkt de handleidingen met de software (zie de tabel).
3. **Herstellen.** Een knop of schermtekst die alleen een andere naam kreeg, vervangt het script
   zelf in de bron. Al het andere komt als bevinding in het controlerapport.
4. **Beoordelen.** Een mens leest het rapport, bekijkt de verschilbeelden en past de tekst aan waar
   de werkwijze veranderde.
5. **Bouwen.** Word, PDF en HTML opnieuw, met de nieuwe softwareversie op de titelpagina.

| Controle | Vindt | Gevolg |
|---|---|---|
| Koppelingen | een scherm, recht of schermafdruk die niet (meer) bestaat; een taak waarvoor de rol het recht niet heeft | blokkeert de nieuwe versie van de handleiding |
| Schermteksten | een vetgedrukte schermtekst die niet in de software voorkomt | nalezen |
| Gewijzigd sinds de vorige versie | hernoemde of verdwenen schermteksten, gewijzigde hulp per scherm, gewijzigde schermsjablonen | hernoemen gaat automatisch; de rest nalezen |
| Schermafdrukken | opnames die meer dan 0,2 % van het beeld verschillen, met een verschilbeeld | de tekst bij die stap nalezen |
| Niet noemen | woorden die de organisatie (nog) niet wil noemen, en de productnaam | blokkeert |

> **Tip:** De opdracht is `.claude/skills/role-manuals/scripts/update.sh <werkmap>`, vanuit de
> map met de software. Het rapport staat daarna in `evidence/check.md`, de verschilbeelden in
> `evidence/diff/`.

## 9. Risico's en maatregelen

| Risico | Maatregel |
|---|---|
| Medewerkers zien het als extra werk | Sleutelgebruikers uit het eigen team; laat zien wat het oplevert; oefengesprek vóór de eerste dag |
| Een deel van de {{participants}} is minder digitaal vaardig | Pagina's en uitleg op B1; het proefgesprek; andere contactmogelijkheden op de pagina |
| De handleiding veroudert | Eén bron, controle bij elke softwareversie, tijdloze tekst |
| Te veel tegelijk | Groep voor groep live; lessen van de vorige groep in de volgende training |
| Het videogesprek lukt niet | Proefgesprek vooraf; "wat als" in elke handleiding; de medewerker geeft de afspraak vrij en de deelnemer maakt een nieuwe |

## 10. Ontwerp: hulp in de software, op het juiste scherm

Nu staan de handleidingen los van de software: als Word en PDF, voor training en als bewijs. Het
ontwerp gaat verder. Dezelfde inhoud verschijnt in {{product}} zelf, op het scherm waar iemand op
dat moment werkt, en alleen voor wie de taak mag uitvoeren. De context bepaalt wat je ziet:

| Signaal | Waar het vandaan komt | Wat het bepaalt |
|---|---|---|
| Rol en rechten | de rechtenmatrix van de software | welke taken iemand te zien krijgt |
| Scherm | het hulponderwerp van het scherm | welke taken en welke stap van een walkthrough passen |
| Toestand | wat het scherm toont, zoals een tekort of een groep met de eigen wachtrij | welke "wat als" er nu toe doet |
| Taal | de taalkeuze van de gebruiker | Nederlandse of Engelse tekst |
| Organisatie | naam, huisstijl, contactgegevens en eigen teksten | de woorden en de servicedesk in de hulp |

Hoe het eruitziet:

- **In het beheerportaal** krijgt elk scherm naast de bestaande hulp (een korte hint, de (i) bij
  een veld en het onderwerp in de ingebouwde handleiding) een vierde laag: **Hoe doe ik …**, de
  taken van dat scherm voor jouw rol.
- **Walkthroughs** lopen over schermen heen. Op elk scherm staat dan "Stap 2 van 5" met vorige en
  volgende, zonder pop-up en ook zonder JavaScript.
- **Op de pagina's voor {{participants}}** staat per stap een uitklapblok **Hulp bij deze stap**,
  en per groep een kort blok met vragen en antwoorden. Nooit een link naar buiten: de hulp staat op
  de pagina zelf.
- **Schermafdrukken** maakt de software bij elke nieuwe versie zelf, met verzonnen gegevens. Ze
  gaan mee met de versie, net als de handleidingen als PDF.

De bouwstraat maakt nu al de koppeling die dat mogelijk maakt: `build/help-index.json` noemt per
scherm de taken per rol, met het recht en de plek in de handleiding. Het voorstel voor de software
wordt een besluit (ADR) samen met de eerste bouwstap: **Hoe doe ik …** op twee schermen
(bezetting en wachtrij), met tests die de koppelingen bewaken.

## 11. Volgende stappen

1. Concept bespreken met de sleutelgebruikers van elke rol.
2. De schermafdrukken die Webex Meetings nodig hebben opnemen in de testomgeving.
3. Lezerstest per rol plannen en uitvoeren.
4. Kiezen hoe de dienst heet en hoe hij eruitziet; daarna de branding in de bouwstraat zetten.
5. Bij elke nieuwe softwareversie `update.sh` draaien en het controlerapport afhandelen.
6. Het besluitvoorstel voor hulp in de software bespreken; bij akkoord de eerste bouwstap plannen.
7. De HTML-versie publiceren op het intranet en bij de link op de website.
